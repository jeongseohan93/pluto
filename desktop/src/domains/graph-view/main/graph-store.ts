/**
 * Reads `.aidev/graph/graph.db`. Read-only, and never anything else.
 *
 * 3조 — 화면은 비추기만 한다. That is not just a convention here: the handle is
 * opened with `readOnly: true`, so a bug in this app cannot write the Function
 * DB even by accident. The pipeline owns that file; Pluto borrows it.
 *
 * The whole driver sits behind `openReadonly` for one reason: `node:sqlite` is
 * a built-in of Node 22.5+, but whether a given Electron build ships it is the
 * Electron build's business. If it is missing, that one function answers
 * `no-sqlite` and the screen says so — and swapping in `better-sqlite3` later
 * means replacing that function and nothing else. Every query below is a
 * transcription of a `GraphDB` method in `aidev/graph/db.py`.
 *
 * Nothing here throws. The file is being replaced by another process while we
 * read it, so missing / locked / half-written / wrong-schema are all expected
 * and each one is a sentence on screen rather than a broken panel.
 */
import { existsSync, statSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import type {
  GraphCallRow,
  GraphCallerRow,
  GraphEdge,
  GraphFileEdge,
  GraphFileGroup,
  GraphFunction,
  GraphIndexResult,
  GraphMeta,
  GraphNodeDetail,
  GraphTag
} from '../types'

/** `aidev/graph/db.py` GRAPH_SCHEMA. A different number is a rebuild, not a migration. */
const GRAPH_SCHEMA = '1'

/** `aidev/graph/build.py` AIDEV_DIRNAME / GRAPH_DIRNAME / DB_FILENAME / DIRTY_FILENAME. */
const GRAPH_SUBDIR = ['.aidev', 'graph']
const DB_FILENAME = 'graph.db'
const DIRTY_FILENAME = 'dirty'

/** A caller list past this length is a scroll nobody reads; the rest is counted. */
const MAX_CALLERS = 200

interface SqliteStatement {
  all(...params: unknown[]): Record<string, unknown>[]
}

interface SqliteDatabase {
  prepare(sql: string): SqliteStatement
  close(): void
}

type DatabaseSyncCtor = new (path: string, options?: { readOnly?: boolean }) => SqliteDatabase

// A require that works whichever module system this file ends up compiled into.
// A bare `require` would be undefined under ESM and the driver would look
// missing when it is merely unreachable; `import` cannot be wrapped in a try,
// and the whole point here is a question that may be answered "no". The base
// path is irrelevant for a built-in module, so `process.cwd()` will do.
const nodeRequire = createRequire(join(process.cwd(), 'graph-store.cjs'))

/**
 * Where this repository's graph lives, by the same rule `graph/build.py` uses.
 *
 * @param repoRoot  the repository or worktree the app has open
 */
export function graphPaths(repoRoot: string): { dir: string; db: string; dirtyMarker: string } {
  const dir = join(repoRoot, ...GRAPH_SUBDIR)
  return { dir, db: join(dir, DB_FILENAME), dirtyMarker: join(dir, DIRTY_FILENAME) }
}

/**
 * The whole index: meta, one group per parsed file, every function in it.
 *
 * The two edge lists ride along on purpose. This is read once per mount and
 * once per finished build, so aggregating here is the "compute it once" the
 * requirement asks for: the canvas never scans the graph to draw a frame, and
 * `GROUP BY` in SQLite beats counting 8669 rows in the renderer either way.
 *
 * @param repoRoot  the repository or worktree the app has open
 * @flow  no file -> no-graph ; open fails -> unreadable/no-sqlite ; wrong
 *        schema -> schema (the screen offers Rebuild) ; else read meta, files
 *        and functions, group the functions under their file, count spec
 *        coverage over non-test functions, and aggregate the call edges
 * 주요 내부 변수: byPath(파일별 함수 묶음), meta(meta 테이블 그대로)
 */
export function readGraphIndex(repoRoot: string): GraphIndexResult {
  const paths = graphPaths(repoRoot)
  const empty = {
    dbPath: paths.db,
    meta: null,
    files: [] as GraphFileGroup[],
    fileEdges: [] as GraphFileEdge[],
    edges: [] as GraphEdge[]
  }

  if (!existsSync(paths.db)) {
    return { ok: false, problem: 'no-graph', detail: 'no graph.db yet', ...empty }
  }

  const opened = openReadonly(paths.db)
  if (!opened.db) {
    return { ok: false, problem: opened.problem, detail: opened.detail, ...empty }
  }

  try {
    const meta = readMeta(opened.db)
    if ((meta.schema || '0') !== GRAPH_SCHEMA) {
      return {
        ok: false,
        problem: 'schema',
        detail: `graph.db is schema ${meta.schema || '0'}, this build reads ${GRAPH_SCHEMA}`,
        ...empty
      }
    }

    const fileRows = opened.db
      .prepare('SELECT path, lang FROM files WHERE parsed = 1 ORDER BY path')
      .all()
    const fnRows = opened.db
      .prepare(
        `SELECT id, qualname, name, path, lang, lineno, end_lineno, signature, kind,
                summary, has_spec, is_test
           FROM functions ORDER BY path, lineno`
      )
      .all()

    const byPath = new Map<string, GraphFunction[]>()
    let functions = 0
    let specTotal = 0
    let specCovered = 0
    for (const row of fnRows) {
      const fn = toFunction(row)
      const bucket = byPath.get(fn.path)
      if (bucket) bucket.push(fn)
      else byPath.set(fn.path, [fn])
      functions += 1
      // Tests are excluded from coverage on purpose: `specs.py` exempts them,
      // so counting them would show a percentage the checker does not use.
      if (!fn.isTest) {
        specTotal += 1
        if (fn.hasSpec) specCovered += 1
      }
    }

    const files: GraphFileGroup[] = fileRows.map((row) => {
      const path = text(row.path)
      return { path, lang: text(row.lang), functions: byPath.get(path) ?? [] }
    })
    // A function whose file row is missing (a half-written update) still has to
    // appear — the graph must not quietly lose a node it holds an id for.
    const known = new Set(files.map((file) => file.path))
    for (const [path, fns] of byPath) {
      if (!known.has(path)) files.push({ path, lang: fns[0]?.lang ?? '', functions: fns })
    }
    files.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0))

    // `JOIN functions dst` is the filter that matters: it drops both the
    // unresolved calls (resolved_id IS NULL) and the ones pointing at an id
    // that no longer exists, so the screen only ever hears about real nodes.
    const fileEdges: GraphFileEdge[] = opened.db
      .prepare(
        `SELECT src.path AS from_path, dst.path AS to_path, COUNT(*) AS weight
           FROM calls c
           JOIN functions src ON src.id = c.caller_id
           JOIN functions dst ON dst.id = c.resolved_id
          WHERE src.path <> dst.path
          GROUP BY src.path, dst.path
          ORDER BY weight DESC, from_path, to_path`
      )
      .all()
      .map((row) => ({ from: text(row.from_path), to: text(row.to_path), weight: int(row.weight) }))

    const edges: GraphEdge[] = opened.db
      .prepare(
        `SELECT DISTINCT c.caller_id AS from_id, c.resolved_id AS to_id
           FROM calls c
           JOIN functions src ON src.id = c.caller_id
           JOIN functions dst ON dst.id = c.resolved_id
          WHERE c.caller_id <> c.resolved_id`
      )
      .all()
      .map((row) => ({ from: int(row.from_id), to: int(row.to_id) }))

    return {
      ok: true,
      dbPath: paths.db,
      meta: {
        schema: meta.schema,
        repo: meta.repo,
        baseCommit: meta.base_commit,
        branch: meta.branch,
        builtAt: meta.built_at,
        updatedAt: meta.updated_at,
        aidevVersion: meta.aidev_version,
        dirty: meta.dirty === '1',
        markedStale: existsSync(paths.dirtyMarker),
        files: files.length,
        functions,
        specCovered,
        specTotal
      } satisfies GraphMeta,
      files,
      fileEdges,
      edges
    }
  } catch (err) {
    return { ok: false, problem: 'unreadable', detail: String(err), ...empty }
  } finally {
    closeQuietly(opened.db)
  }
}

/**
 * One function's spec, its calls and its call sites — the side panel's payload.
 *
 * @param repoRoot  the repository or worktree the app has open
 * @param id        the `functions.id` the renderer clicked
 * @flow  no readable db -> null ; no such id -> null ; else tags, calls, and
 *        the call sites that name it, keeping the ones that resolve to it and
 *        the ones the engine deliberately left unresolved
 * 주요 내부 변수: rows(callers 원본), kept(내 것으로 남긴 호출부)
 */
export function readGraphNode(repoRoot: string, id: number): GraphNodeDetail | null {
  if (!Number.isInteger(id) || id < 0) return null
  const paths = graphPaths(repoRoot)
  if (!existsSync(paths.db)) return null

  const opened = openReadonly(paths.db)
  if (!opened.db) return null

  try {
    const found = opened.db
      .prepare(
        `SELECT id, qualname, name, path, lang, lineno, end_lineno, signature, kind,
                summary, has_spec, is_test
           FROM functions WHERE id = ?`
      )
      .all(id)
    if (found.length === 0) return null
    const fn = toFunction(found[0])

    const tags: GraphTag[] = opened.db
      .prepare('SELECT tag, value, core, lineno FROM tags WHERE func_id = ? ORDER BY lineno, rowid')
      .all(id)
      .map((row) => ({
        tag: text(row.tag),
        value: text(row.value),
        core: int(row.core) === 1,
        lineno: int(row.lineno)
      }))

    const calls: GraphCallRow[] = opened.db
      .prepare(
        `SELECT c.lineno AS call_line, c.callee_name AS callee, c.callee_raw AS raw,
                c.resolved_id AS target_id, t.path AS target_path,
                t.lineno AS target_line, t.qualname AS target_qualname
           FROM calls c LEFT JOIN functions t ON t.id = c.resolved_id
          WHERE c.caller_id = ? ORDER BY c.lineno, c.id`
      )
      .all(id)
      .map((row) => ({
        callLine: int(row.call_line),
        callee: text(row.callee),
        raw: text(row.raw),
        targetId: maybeInt(row.target_id),
        targetPath: row.target_path === null ? null : text(row.target_path),
        targetLine: maybeInt(row.target_line),
        targetQualname: row.target_qualname === null ? null : text(row.target_qualname)
      }))

    const rows = opened.db
      .prepare(
        `SELECT f.id AS caller_id, f.path AS path, f.qualname AS caller,
                f.lineno AS caller_line, c.lineno AS call_line, c.resolved_id AS target_id
           FROM calls c JOIN functions f ON f.id = c.caller_id
          WHERE c.callee_name = ? ORDER BY f.path, c.lineno`
      )
      .all(fn.name)
    // `db.callers` keeps unresolved edges on purpose: when two files define the
    // same name the engine refuses to pick one, and the call site is still
    // where a human has to look. Rows resolved to *someone else* are not ours.
    const kept: GraphCallerRow[] = []
    for (const row of rows) {
      const target = maybeInt(row.target_id)
      if (target !== null && target !== fn.id) continue
      kept.push({
        callerId: int(row.caller_id),
        path: text(row.path),
        caller: text(row.caller),
        callerLine: int(row.caller_line),
        callLine: int(row.call_line),
        unresolved: target === null
      })
    }

    return {
      fn,
      tags,
      calls,
      callers: kept.slice(0, MAX_CALLERS),
      callersTruncated: Math.max(0, kept.length - MAX_CALLERS)
    }
  } catch {
    return null
  } finally {
    closeQuietly(opened.db)
  }
}

// ------------------------------------------------------------------- driver

/**
 * The one place a SQLite driver is named. Opened per request and closed after.
 *
 * Per request, not cached: the pipeline rebuilds this file underneath us, and a
 * handle held open across a rebuild answers from a file that no longer exists.
 *
 * @param path  the graph.db to open
 * @flow  no driver in this runtime -> no-sqlite ; open throws -> unreadable
 */
export function openReadonly(path: string): {
  db: SqliteDatabase | null
  problem?: 'unreadable' | 'no-sqlite'
  detail?: string
} {
  const DatabaseSync = loadDatabaseSync()
  if (!DatabaseSync) {
    return {
      db: null,
      problem: 'no-sqlite',
      detail: 'this runtime has no node:sqlite (Node 22.5+ builds it in)'
    }
  }
  try {
    return { db: new DatabaseSync(path, { readOnly: true }) }
  } catch (err) {
    return { db: null, problem: 'unreadable', detail: String(err) }
  }
}

/**
 * `node:sqlite`'s DatabaseSync, or null where the runtime does not ship it.
 *
 * `require` rather than `import`: this has to be a question that can be
 * answered with "no" at runtime, and a static import of a missing built-in is
 * a module that fails to load instead. Takes no arguments.
 */
function loadDatabaseSync(): DatabaseSyncCtor | null {
  try {
    const mod = nodeRequire('node:sqlite') as { DatabaseSync?: DatabaseSyncCtor }
    return mod.DatabaseSync ?? null
  } catch {
    return null
  }
}

/** True when this runtime can read a graph at all — the tests skip without it. */
export function hasSqlite(): boolean {
  return loadDatabaseSync() !== null
}

/**
 * When the DB file itself last changed, for a "read at" line. 0 when unknown.
 *
 * @param repoRoot  the repository or worktree the app has open
 */
export function graphFileMtime(repoRoot: string): number {
  try {
    return statSync(graphPaths(repoRoot).db).mtimeMs
  } catch {
    return 0
  }
}

// ------------------------------------------------------------------ helpers

/**
 * The `meta` table as a plain record, with every key this app reads present.
 *
 * @param db  an open read-only handle
 */
function readMeta(db: SqliteDatabase): Record<string, string> {
  const out: Record<string, string> = {
    schema: '',
    repo: '',
    base_commit: '',
    branch: '',
    built_at: '',
    updated_at: '',
    aidev_version: '',
    dirty: ''
  }
  for (const row of db.prepare('SELECT key, value FROM meta').all()) {
    out[text(row.key)] = text(row.value)
  }
  return out
}

/**
 * One `functions` row in the app's spelling.
 *
 * @param row  the row as SQLite returned it
 */
function toFunction(row: Record<string, unknown>): GraphFunction {
  return {
    id: int(row.id),
    qualname: text(row.qualname),
    name: text(row.name),
    path: text(row.path),
    lang: text(row.lang),
    lineno: int(row.lineno),
    endLineno: int(row.end_lineno),
    signature: text(row.signature),
    kind: text(row.kind),
    summary: text(row.summary),
    hasSpec: int(row.has_spec) === 1,
    isTest: int(row.is_test) === 1
  }
}

/** Closing must never be the thing that throws. */
function closeQuietly(db: SqliteDatabase | null): void {
  try {
    db?.close()
  } catch {
    // the handle is going away with this call either way
  }
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : value === null || value === undefined ? '' : String(value)
}

function int(value: unknown): number {
  const n = typeof value === 'bigint' ? Number(value) : Number(value)
  return Number.isFinite(n) ? n : 0
}

function maybeInt(value: unknown): number | null {
  return value === null || value === undefined ? null : int(value)
}

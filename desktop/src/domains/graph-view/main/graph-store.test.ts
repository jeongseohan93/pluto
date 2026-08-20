/**
 * The read-only Function DB reader, against a real SQLite file.
 *
 * The fixture is written with the same schema `aidev/graph/db.py` writes, so
 * what is being checked is the transcription of those queries and not a mock of
 * them. Every failure path is checked too: this reader is pointed at a file
 * another process replaces, and a screen that says why is the whole product.
 *
 * Skipped where the runtime has no `node:sqlite` — which is exactly the case
 * `openReadonly` answers `no-sqlite` for.
 */
import { after, describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

import {
  graphPaths,
  hasSqlite,
  pathInGraph,
  readGraphIndex,
  readGraphNode,
  readGraphTrace
} from './graph-store'
import { clampTraceDepth } from '../types'

/** The fixture is written with the same driver the reader reads it with. */
const nodeRequire = createRequire(join(process.cwd(), 'graph-store.test.cjs'))

interface WritableDb {
  exec(sql: string): void
  close(): void
}

const roots: string[] = []
const available = hasSqlite()

after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

function tempRoot(): string {
  const root = mkdtempSync(join(tmpdir(), 'pluto-graph-'))
  roots.push(root)
  return root
}

/** `aidev/graph/db.py` SCHEMA, as a build would leave it. */
const SCHEMA = `
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE files (path TEXT PRIMARY KEY, lang TEXT, hash TEXT, size INTEGER, mtime REAL,
                    parsed INTEGER, error TEXT, funcs INTEGER, scanned_at TEXT);
CREATE TABLE functions (id INTEGER PRIMARY KEY, qualname TEXT, name TEXT, path TEXT, lang TEXT,
                        lineno INTEGER, end_lineno INTEGER, params TEXT, signature TEXT, kind TEXT,
                        summary TEXT, spec TEXT, has_spec INTEGER, is_test INTEGER);
CREATE TABLE calls (id INTEGER PRIMARY KEY, caller_id INTEGER, callee_name TEXT, callee_raw TEXT,
                    lineno INTEGER, resolved_id INTEGER);
CREATE TABLE tags (func_id INTEGER, tag TEXT, value TEXT, core INTEGER, lineno INTEGER);
`

/**
 * A repository with a graph in it, built by hand at schema 1.
 *
 * Contents: two parsed files and one that failed to parse, four functions (one
 * without a spec, one a test), one call within a file, two across one, one
 * ambiguous call, and one call that resolves to an id that is not there.
 */
function repoWithGraph(schema = '1'): string {
  const root = tempRoot()
  const paths = graphPaths(root)
  mkdirSync(paths.dir, { recursive: true })

  const Ctor = (nodeRequire('node:sqlite') as { DatabaseSync: new (p: string) => WritableDb })
    .DatabaseSync
  const db = new Ctor(paths.db)
  db.exec(SCHEMA)
  db.exec(`
    INSERT INTO meta (key, value) VALUES
      ('schema', '${schema}'), ('repo', '/repo'), ('base_commit', '411c3e9deadbeef'),
      ('branch', 'slice/x'), ('built_at', '2026-08-19T18:16:24+09:00'),
      ('updated_at', '2026-08-19T18:20:00+09:00'), ('dirty', '1'), ('aidev_version', '0.7.0');

    INSERT INTO files (path, lang, parsed, funcs) VALUES
      ('aidev/pipeline.py', 'py', 1, 2),
      ('aidev/cli.py', 'py', 1, 1),
      ('tests/test_x.py', 'py', 1, 1),
      ('aidev/broken.py', 'py', 0, 0);

    INSERT INTO functions (id, qualname, name, path, lang, lineno, end_lineno, signature, kind,
                           summary, has_spec, is_test) VALUES
      (1, 'run_pipeline', 'run_pipeline', 'aidev/pipeline.py', 'py', 10, 40,
       'run_pipeline(args, repo)', 'function', 'Drive one requirement through the stages.', 1, 0),
      (2, 'say', 'say', 'aidev/pipeline.py', 'py', 100, 104, 'say(text)', 'function', '', 0, 0),
      (3, 'main', 'main', 'aidev/cli.py', 'py', 5, 20, 'main(argv)', 'function',
       'The entry point.', 1, 0),
      (4, 'test_runs', 'test_runs', 'tests/test_x.py', 'py', 3, 9, 'test_runs()', 'function',
       'checks it', 1, 1);

    INSERT INTO calls (id, caller_id, callee_name, callee_raw, lineno, resolved_id) VALUES
      (1, 1, 'say', 'say', 12, 2),
      (2, 3, 'run_pipeline', 'pipeline.run_pipeline', 8, 1),
      (3, 4, 'run_pipeline', 'run_pipeline', 4, NULL),
      (4, 2, 'run_pipeline', 'other.run_pipeline', 101, 999),
      (5, 2, 'main', 'main', 102, 3),
      (6, 2, 'main', 'cli.main', 103, 3);

    INSERT INTO tags (func_id, tag, value, core, lineno) VALUES
      (1, 'param', 'args  the parsed arguments', 1, 11),
      (1, 'param', 'repo  the repository', 1, 12),
      (1, 'flow', 'plan -> approval -> implement -> test', 1, 13),
      (1, 'note', 'v0.7', 0, 14);
  `)
  db.close()
  return root
}

describe('readGraphIndex', { skip: !available }, () => {
  test('counts files, functions and spec coverage over non-test functions', () => {
    const index = readGraphIndex(repoWithGraph())
    assert.equal(index.ok, true)
    assert.equal(index.meta?.functions, 4)
    // Three parsed files carry functions; the unparsed one is not a box.
    assert.equal(index.files.length, 3)
    assert.equal(index.meta?.files, 3)
    // run_pipeline and main have specs; say does not; the test is exempt.
    assert.equal(index.meta?.specTotal, 3)
    assert.equal(index.meta?.specCovered, 2)
  })

  test('carries the freshness the header shows', () => {
    const root = repoWithGraph()
    const index = readGraphIndex(root)
    assert.equal(index.meta?.baseCommit, '411c3e9deadbeef')
    assert.equal(index.meta?.branch, 'slice/x')
    assert.equal(index.meta?.builtAt, '2026-08-19T18:16:24+09:00')
    assert.equal(index.meta?.dirty, true)
    assert.equal(index.meta?.markedStale, false)

    writeFileSync(graphPaths(root).dirtyMarker, 'now\nstage commit\n', 'utf8')
    assert.equal(readGraphIndex(root).meta?.markedStale, true)
  })

  test('functions arrive grouped under their file, in coordinate order', () => {
    const index = readGraphIndex(repoWithGraph())
    const pipeline = index.files.find((f) => f.path === 'aidev/pipeline.py')
    assert.deepEqual(
      pipeline?.functions.map((f) => f.name),
      ['run_pipeline', 'say']
    )
    assert.equal(pipeline?.functions[1].hasSpec, false)
    assert.equal(index.files.find((f) => f.path === 'tests/test_x.py')?.functions[0].isTest, true)
  })

  test('no graph at all is a sentence, not an exception', () => {
    const index = readGraphIndex(tempRoot())
    assert.equal(index.ok, false)
    assert.equal(index.problem, 'no-graph')
    assert.match(index.dbPath, /graph\.db$/)
    assert.deepEqual(index.files, [])
  })

  test('a file that is not SQLite at all is unreadable, not a crash', () => {
    const root = tempRoot()
    const paths = graphPaths(root)
    mkdirSync(paths.dir, { recursive: true })
    writeFileSync(paths.db, 'this is not a database', 'utf8')
    const index = readGraphIndex(root)
    assert.equal(index.ok, false)
    assert.equal(index.problem, 'unreadable')
    assert.ok(index.detail)
  })

  test('another schema asks for a rebuild rather than guessing', () => {
    const index = readGraphIndex(repoWithGraph('2'))
    assert.equal(index.ok, false)
    assert.equal(index.problem, 'schema')
    assert.match(index.detail ?? '', /schema 2/)
  })

  test('file links are counted per pair, heaviest first', () => {
    const index = readGraphIndex(repoWithGraph())
    // say -> main twice, main -> run_pipeline once. run_pipeline -> say is
    // inside one file and so is not a link between two; the ambiguous call and
    // the one aimed at a missing id are not links at all.
    assert.deepEqual(index.fileEdges, [
      { from: 'aidev/pipeline.py', to: 'aidev/cli.py', weight: 2 },
      { from: 'aidev/cli.py', to: 'aidev/pipeline.py', weight: 1 }
    ])
  })

  test('the function edges are the resolved calls, each pair once', () => {
    const index = readGraphIndex(repoWithGraph())
    const pairs = index.edges.map((e) => `${e.from}->${e.to}`).sort()
    // (2,3) is written twice in `calls` and arrives once. 999 is nobody, and
    // the unresolved call points at nothing to draw.
    assert.deepEqual(pairs, ['1->2', '2->3', '3->1'])
  })

  test('a graph that could not be read still answers with empty edge lists', () => {
    const missing = readGraphIndex(tempRoot())
    assert.deepEqual(missing.fileEdges, [])
    assert.deepEqual(missing.edges, [])

    const root = tempRoot()
    const paths = graphPaths(root)
    mkdirSync(paths.dir, { recursive: true })
    writeFileSync(paths.db, 'this is not a database', 'utf8')
    const broken = readGraphIndex(root)
    assert.deepEqual(broken.fileEdges, [])
    assert.deepEqual(broken.edges, [])

    const wrong = readGraphIndex(repoWithGraph('2'))
    assert.deepEqual(wrong.fileEdges, [])
    assert.deepEqual(wrong.edges, [])
  })
})

describe('readGraphNode', { skip: !available }, () => {
  test('the spec tags come back in the order they were written', () => {
    const detail = readGraphNode(repoWithGraph(), 1)
    assert.ok(detail)
    assert.equal(detail.fn.name, 'run_pipeline')
    assert.deepEqual(
      detail.tags.map((t) => t.tag),
      ['param', 'param', 'flow', 'note']
    )
    assert.equal(detail.tags[0].core, true)
    assert.equal(detail.tags[3].core, false)
  })

  test('calls carry the target’s coordinates when the engine resolved them', () => {
    const detail = readGraphNode(repoWithGraph(), 1)
    assert.equal(detail?.calls.length, 1)
    assert.equal(detail?.calls[0].callee, 'say')
    assert.equal(detail?.calls[0].targetId, 2)
    assert.equal(detail?.calls[0].targetPath, 'aidev/pipeline.py')
  })

  test('callers keep the unresolved sites and drop the ones aimed elsewhere', () => {
    const detail = readGraphNode(repoWithGraph(), 1)
    assert.ok(detail)
    const callers = detail.callers.map((c) => `${c.caller}:${c.unresolved}`)
    // main resolves to us; the test's call is ambiguous and kept; say's call
    // resolved to a different function and is not ours.
    assert.deepEqual(callers.sort(), ['main:false', 'test_runs:true'])
    assert.equal(detail.callersTruncated, 0)
  })

  test('an id that is not there, or not an id, is null', () => {
    const root = repoWithGraph()
    assert.equal(readGraphNode(root, 9999), null)
    assert.equal(readGraphNode(root, -1), null)
    assert.equal(readGraphNode(root, 1.5), null)
    assert.equal(readGraphNode(tempRoot(), 1), null)
  })
})

/**
 * A repository whose one call names something the repository does not define.
 *
 * `len` and `print` are the ordinary case in a real graph, and they are not a
 * broken chain — they are where the repository ends. Kept apart from
 * `repoWithGraph` so that fixture's counts, which several tests above spell out,
 * stay exactly as they were.
 */
function repoWithOutsideCall(): string {
  const root = tempRoot()
  const paths = graphPaths(root)
  mkdirSync(paths.dir, { recursive: true })

  const Ctor = (nodeRequire('node:sqlite') as { DatabaseSync: new (p: string) => WritableDb })
    .DatabaseSync
  const db = new Ctor(paths.db)
  db.exec(SCHEMA)
  db.exec(`
    INSERT INTO meta (key, value) VALUES ('schema', '1');
    INSERT INTO files (path, lang, parsed, funcs) VALUES ('aidev/x.py', 'py', 1, 1);
    INSERT INTO functions (id, qualname, name, path, lang, lineno, end_lineno, signature, kind,
                           summary, has_spec, is_test) VALUES
      (1, 'counts', 'counts', 'aidev/x.py', 'py', 1, 5, 'counts(xs)', 'function', 'how many', 1, 0);
    INSERT INTO calls (id, caller_id, callee_name, callee_raw, lineno, resolved_id) VALUES
      (1, 1, 'len', 'len', 2, NULL),
      (2, 1, 'print', 'print', 3, NULL);
  `)
  db.close()
  return root
}

describe('readGraphTrace', { skip: !available }, () => {
  test('one ring out is the root and what it calls', () => {
    const trace = readGraphTrace(repoWithGraph(), 1, 'calls', 1)
    assert.ok(trace)
    assert.equal(trace.rootId, 1)
    assert.equal(trace.direction, 'calls')
    assert.deepEqual(trace.nodes, [
      { id: 1, depth: 0 },
      { id: 2, depth: 1 }
    ])
    assert.deepEqual(trace.edges, [{ from: 1, to: 2, depth: 1 }])
    assert.equal(trace.truncated, 0)
  })

  test('three rings reach main, and the cycle back does not recurse forever', () => {
    const trace = readGraphTrace(repoWithGraph(), 1, 'calls', 3)
    assert.ok(trace)
    // run_pipeline -> say -> main -> run_pipeline. The root is reached again at
    // depth 3 and stays 0, because a node's depth is its *shortest* one.
    assert.deepEqual(trace.nodes, [
      { id: 1, depth: 0 },
      { id: 2, depth: 1 },
      { id: 3, depth: 2 }
    ])
    const pairs = trace.edges.map((e) => `${e.from}->${e.to}`).sort()
    // say -> main is written twice in `calls` and is one curve. 3 -> 1 closes
    // the cycle and is drawn, because 3 was expanded.
    assert.deepEqual(pairs, ['1->2', '2->3', '3->1'])
  })

  test('a node at the last ring has no curve leaving it', () => {
    const trace = readGraphTrace(repoWithGraph(), 1, 'calls', 2)
    assert.ok(trace)
    // 3 is at depth 2 = the limit, so 3 -> 1 was never followed and is not drawn.
    assert.deepEqual(
      trace.edges.map((e) => `${e.from}->${e.to}`).sort(),
      ['1->2', '2->3']
    )
  })

  test('a call the engine would not aim is where the chain stops', () => {
    const trace = readGraphTrace(repoWithGraph(), 1, 'calls', 3)
    assert.ok(trace)
    // say's `other.run_pipeline` resolves to id 999, which is nobody.
    assert.deepEqual(trace.breaks, [
      { atId: 2, depth: 1, callee: 'run_pipeline', count: 1, candidates: 1 }
    ])
    assert.equal(trace.external, 0)
  })

  test('a node the chain only reached is not asked what it could not follow', () => {
    const trace = readGraphTrace(repoWithGraph(), 1, 'calls', 1)
    // say is at depth 1 = the limit: it was never expanded, so its unfollowable
    // call is not a stop of *this* chain.
    assert.deepEqual(trace?.breaks, [])
  })

  test('a call to something this repository does not define is not a break', () => {
    const trace = readGraphTrace(repoWithOutsideCall(), 1, 'calls', 3)
    assert.ok(trace)
    assert.deepEqual(trace.breaks, [])
    // `len` and `print` — the edge of the repository, counted and not drawn.
    assert.equal(trace.external, 2)
  })

  test('the other direction walks the callers', () => {
    const trace = readGraphTrace(repoWithGraph(), 1, 'callers', 2)
    assert.ok(trace)
    assert.deepEqual(trace.nodes, [
      { id: 1, depth: 0 },
      { id: 3, depth: 1 },
      { id: 2, depth: 2 }
    ])
    // The arrows still point caller -> callee; only the walk was backwards.
    assert.deepEqual(
      trace.edges.map((e) => `${e.from}->${e.to}`).sort(),
      ['2->3', '3->1']
    )
  })

  test('walking back, an ambiguous call site is where it stops', () => {
    const trace = readGraphTrace(repoWithGraph(), 1, 'callers', 1)
    assert.ok(trace)
    // Two call sites name run_pipeline without reaching it: the test's
    // unresolved one, and say's, which points at an id that is not there.
    assert.deepEqual(trace.breaks, [
      { atId: 1, depth: 0, callee: 'run_pipeline', count: 2, candidates: 1 }
    ])
    // Walking back, every unfollowable site names something we know by
    // definition, so there is nothing outside the repository to count.
    assert.equal(trace.external, 0)
  })

  test('a function nothing resolves out of is a chain of one, not a null', () => {
    const trace = readGraphTrace(repoWithGraph(), 4, 'calls', 3)
    assert.ok(trace)
    assert.deepEqual(trace.nodes, [{ id: 4, depth: 0 }])
    assert.deepEqual(trace.edges, [])
    assert.equal(trace.breaks.length, 1)
    assert.equal(trace.breaks[0].atId, 4)
  })

  test('a depth outside the range is clamped rather than refused', () => {
    const root = repoWithGraph()
    assert.equal(readGraphTrace(root, 1, 'calls', 0)?.depth, 1)
    assert.equal(readGraphTrace(root, 1, 'calls', 99)?.depth, 5)
    assert.equal(readGraphTrace(root, 1, 'calls', Number.NaN)?.depth, 3)
  })

  test('an id that is not there, or not an id, is null', () => {
    const root = repoWithGraph()
    assert.equal(readGraphTrace(root, 9999, 'calls', 3), null)
    assert.equal(readGraphTrace(root, -1, 'calls', 3), null)
    assert.equal(readGraphTrace(root, 1.5, 'calls', 3), null)
    assert.equal(readGraphTrace(tempRoot(), 1, 'calls', 3), null)
  })
})

describe('clampTraceDepth', () => {
  test('the range the UI offers is the range main allows', () => {
    assert.equal(clampTraceDepth(1), 1)
    assert.equal(clampTraceDepth(3), 3)
    assert.equal(clampTraceDepth(5), 5)
  })

  test('past either end lands on that end', () => {
    assert.equal(clampTraceDepth(0), 1)
    assert.equal(clampTraceDepth(-1), 1)
    assert.equal(clampTraceDepth(9), 5)
  })

  test('something that is not a number reads as the default', () => {
    assert.equal(clampTraceDepth(Number.NaN), 3)
    assert.equal(clampTraceDepth(Number.POSITIVE_INFINITY), 3)
  })

  test('a depth between two rings is one of them, not a fraction', () => {
    assert.equal(clampTraceDepth(2.7), 3)
    assert.equal(clampTraceDepth(2.2), 2)
  })
})

describe('pathInGraph', { skip: !available }, () => {
  test('a file the graph parsed is one this repository knows', () => {
    const root = repoWithGraph()
    assert.equal(pathInGraph(root, 'aidev/pipeline.py'), true)
    assert.equal(pathInGraph(root, 'tests/test_x.py'), true)
  })

  test('a file that failed to parse is still in the repository', () => {
    // `files` holds it with parsed = 0. It draws no box, but a reader who got
    // to its name some other way is still asking about a real file.
    assert.equal(pathInGraph(repoWithGraph(), 'aidev/broken.py'), true)
  })

  test('a path this graph never saw is refused', () => {
    const root = repoWithGraph()
    assert.equal(pathInGraph(root, 'aidev/nowhere.py'), false)
    assert.equal(pathInGraph(root, '../../etc/passwd'), false)
    assert.equal(pathInGraph(root, ''), false)
  })

  test('no graph at all answers no, rather than throwing', () => {
    assert.equal(pathInGraph(tempRoot(), 'aidev/pipeline.py'), false)
  })
})

describe('without node:sqlite', { skip: available }, () => {
  test('the reader says so instead of pretending there is no graph', () => {
    const root = tempRoot()
    const paths = graphPaths(root)
    mkdirSync(paths.dir, { recursive: true })
    writeFileSync(paths.db, '', 'utf8')
    const index = readGraphIndex(root)
    assert.equal(index.ok, false)
    assert.equal(index.problem, 'no-sqlite')
  })
})

/**
 * Reads `<repo>/.aidev/` the way the Python core reads it, and writes exactly
 * one kind of file back: `slices/<id>/approvals/<stage>.md`.
 *
 * Deliberately free of Electron: everything here is `node:fs` plus types, so it
 * runs under `node --test` without a display. The main process is the only
 * caller — the renderer reaches it through the preload capability bridge.
 *
 * Every read is tolerant in the same sense as `aidev/storage.py`
 * `read_json_tolerant`: the pipeline is replacing these files underneath us, so
 * missing / locked / truncated / not-JSON are all expected and none of them may
 * raise. A poll that throws would take the whole panel down.
 */
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  statSync,
  writeFileSync
} from 'node:fs'
import { dirname, join, resolve, sep } from 'node:path'
import { readMerge, readQuota, readStopped } from '../domains/pipeline/state'
import type {
  ApprovalInput,
  ApprovalResult,
  ApprovalVerdict,
  EpicSliceRef,
  EpicState,
  LiveStatus,
  SliceStage,
  SliceState,
  StageArtifact
} from '../shared/ide'

/** `aidev/pipeline.py` AIDEV_DIRNAME / STATE_FILENAME. */
const AIDEV_DIRNAME = '.aidev'
const STATE_FILENAME = 'state.json'

/** `aidev/storage.py`: a Windows reader can block os.replace for a moment. */
const REPLACE_RETRIES = 20
const REPLACE_RETRY_MS = 50

/** `aidev/reporter.py` STALE_AFTER_S — applies to live.json, nothing else. */
const STALE_AFTER_S = 10

/** Which file a stage asks the human to read before deciding. */
const STAGE_ARTIFACT: Record<string, string> = { plan: 'plan.md' }

const PENDING: Decision = { verdict: 'pending', reason: '' }

export interface Decision {
  verdict: ApprovalVerdict
  reason: string
}

// ------------------------------------------------------------------- reading

export function readTextTolerant(file: string): string | null {
  try {
    return readFileSync(file, 'utf8')
  } catch {
    return null
  }
}

/**
 * Port of `aidev/storage.py` `read_json_tolerant`: missing, locked, truncated,
 * not JSON, or JSON that is not an object all come back as `null`.
 */
export function readJsonTolerant(file: string): Record<string, unknown> | null {
  const text = readTextTolerant(file)
  if (text === null) return null
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return null
  }
  return isRecord(data) ? data : null
}

/**
 * Port of `aidev/pipeline.py` `read_decision` (the first line that is neither
 * blank nor a comment decides). Kept literal on purpose: this is the contract
 * that decides whether the pipeline moves, so the UI must predict it exactly —
 * including the part where anything else means PENDING and stops the scan.
 *
 * `approved: <조건>` keeps its condition, exactly as `read_decision` does: the
 * text after the colon becomes `Decision.reason` and the engine carries it into
 * every later stage's briefing as an approval condition. Dropping it here (as
 * this function used to) made the UI report a conditional approval as an
 * unconditional one.
 *
 * @param text  the approval file's contents
 * @flow  skip blanks and comments -> split the first real line at ':' ->
 *        approved / rejected keep what follows -> anything else is PENDING and
 *        stops the scan
 */
export function parseDecision(text: string): Decision {
  for (const raw of stripBom(text).split(/\r\n|\r|\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#') || line.startsWith('<!--')) continue
    const cut = line.indexOf(':')
    const head = cut === -1 ? line : line.slice(0, cut)
    const rest = cut === -1 ? '' : line.slice(cut + 1)
    const word = head.trim().toLowerCase().replace(/[.!]+$/, '')
    if (word === 'approved' || word === 'approve') return { verdict: 'approved', reason: rest.trim() }
    if (word === 'rejected' || word === 'reject') return { verdict: 'rejected', reason: rest.trim() }
    return { ...PENDING } // anything else: the human is still writing
  }
  return { ...PENDING }
}

export function readDecision(file: string): Decision {
  const text = readTextTolerant(file)
  return text === null ? { ...PENDING } : parseDecision(text)
}

// -------------------------------------------------------------------- slices

export function slicesRoot(repoRoot: string): string {
  return join(repoRoot, AIDEV_DIRNAME, 'slices')
}

export function epicsRoot(repoRoot: string): string {
  return join(repoRoot, AIDEV_DIRNAME, 'epics')
}

export function isAidevRepo(repoRoot: string): boolean {
  return isDirectory(join(repoRoot, AIDEV_DIRNAME))
}

export function listSlices(repoRoot: string): SliceState[] {
  const root = slicesRoot(repoRoot)
  const out = listDirNames(root).map((id) => readSlice(join(root, id), id))
  out.sort(byUpdatedDesc)
  return out
}

/**
 * One slice as `--list` would print it, plus what finishing it needs to know.
 *
 * `merge` / `quota` / `stopped` are carried here rather than fetched per click
 * because the discard guard has to be able to answer for every row in the list,
 * not only for the selected one.
 *
 * @param dir  the slice's directory
 * @param id   its id, which is also the directory name
 * @flow  no readable state.json -> the "(unreadable)" row `--list` prints ->
 *        otherwise every field, with freshness only for a running slice
 */
function readSlice(dir: string, id: string): SliceState {
  const state = readJsonTolerant(join(dir, STATE_FILENAME))
  if (!state) {
    // `--list` prints "(unreadable)" for exactly this case; so do we.
    return {
      id,
      dir,
      status: '(unreadable)',
      waitingStage: null,
      stages: [],
      gates: [],
      updatedAt: null,
      reason: null,
      testVerdict: null,
      epic: null,
      live: null,
      merge: null,
      quota: null,
      stopped: null,
      unreadable: true
    }
  }
  const status = str(state.status) ?? 'pending'
  const waiting = 'waiting_approval:'
  return {
    id,
    dir,
    status,
    waitingStage: status.startsWith(waiting) ? status.slice(waiting.length) : null,
    stages: readStages(state),
    gates: strList(state.gates),
    updatedAt: str(state.updated_at) ?? null,
    reason: str(state.reason) ?? null,
    testVerdict: str(state.test_verdict) ?? null,
    epic: isRecord(state.epic)
      ? { epic_id: str(state.epic.epic_id), index: num(state.epic.index) }
      : null,
    // state.json is written at stage boundaries, not as a heartbeat: applying
    // the 10s rule to it would call every healthy run stale. Only live.json
    // carries freshness, and only a running slice has one worth reading.
    live: status.startsWith('running:') ? liveStatusFor(dir) : null,
    merge: readMerge(state),
    quota: readQuota(state),
    stopped: readStopped(state),
    unreadable: false
  }
}

/**
 * Stage names in declared order, then any key the tool wrote that this build
 * has never heard of. `--list` shows unknown stages rather than stopping, and a
 * reader that hid them would be lying about what the pipeline is doing.
 */
function readStages(state: Record<string, unknown>): SliceStage[] {
  const raw = isRecord(state.stages) ? state.stages : {}
  const order: string[] = []
  for (const name of strList(state.stage_order)) if (!order.includes(name)) order.push(name)
  for (const name of Object.keys(raw)) if (!order.includes(name)) order.push(name)

  return order.map((name) => {
    const entry = isRecord(raw[name]) ? raw[name] : {}
    return {
      name,
      status: str(entry.status) ?? '?',
      approval: str(entry.approval),
      approvalReason: str(entry.approval_reason),
      runId: str(entry.run_id),
      attempts: num(entry.attempts),
      verdict: str(entry.verdict)
    }
  })
}

/**
 * Freshness of the run a slice is inside, under `aidev/reporter.py`'s rule.
 * `run_dir` is an absolute path into the tool's own `data/`, which may not
 * exist on this machine — that is a `null`, not an error.
 */
export function liveStatusFor(sliceDir: string): LiveStatus | null {
  const index = readJsonTolerant(join(sliceDir, 'runs.json'))
  const runs = index && Array.isArray(index.runs) ? index.runs : []
  const last = runs.length ? runs[runs.length - 1] : null
  const runDir = isRecord(last) ? str(last.run_dir) : undefined
  if (!runDir) return null

  const live = readJsonTolerant(join(runDir, 'live.json'))
  const updatedAt = live ? num(live.updated_at) : undefined
  if (updatedAt === undefined) return null

  const status = str(live?.status) ?? ''
  const ageSeconds = Math.max(0, Date.now() / 1000 - updatedAt)
  return {
    status,
    updatedAt,
    ageSeconds,
    stale: status === 'running' && ageSeconds > STALE_AFTER_S
  }
}

// --------------------------------------------------------------------- epics

export function listEpics(repoRoot: string): EpicState[] {
  const root = epicsRoot(repoRoot)
  const out = listDirNames(root).map((id) => readEpic(repoRoot, join(root, id), id))
  out.sort(byUpdatedDesc)
  return out
}

function readEpic(repoRoot: string, dir: string, id: string): EpicState {
  const state = readJsonTolerant(join(dir, STATE_FILENAME))
  if (!state) {
    return {
      id,
      status: '(unreadable)',
      current: null,
      slices: [],
      updatedAt: null,
      unreadable: true
    }
  }
  const entries = Array.isArray(state.slices) ? state.slices : []
  const slices: EpicSliceRef[] = entries.filter(isRecord).map((entry) => {
    const sliceId = str(entry.slice_id) ?? null
    return {
      index: num(entry.index) ?? null,
      title: str(entry.title) ?? '',
      sliceId,
      // `aidev/epic.py` slice_status: the slice's own state.json is the
      // authority, the epic entry only projects it.
      status: (sliceId ? sliceStatus(repoRoot, sliceId) : null) ?? str(entry.status) ?? 'pending'
    }
  })
  return {
    id,
    status: str(state.status) ?? '(unreadable)',
    current: num(state.current) ?? null,
    slices,
    updatedAt: str(state.updated_at) ?? null,
    unreadable: false
  }
}

function sliceStatus(repoRoot: string, sliceId: string): string | null {
  if (!SEGMENT.test(sliceId) || sliceId.includes('..')) return null
  const state = readJsonTolerant(join(slicesRoot(repoRoot), sliceId, STATE_FILENAME))
  return state ? (str(state.status) ?? null) : null
}

// ------------------------------------------------------------------ artifact

/**
 * The text the human is being asked to approve. Never throws on a missing file:
 * the path is still worth showing, because the answer to "where is it?" is the
 * whole point of the viewer.
 */
export function readStageArtifact(
  repoRoot: string,
  sliceId: string,
  stage: string
): StageArtifact {
  const dir = sliceDir(repoRoot, sliceId)
  const name = safeSegment(stage)
  const named = STAGE_ARTIFACT[name]
  let file = join(dir, named ?? 'plan.md')
  if (!named && !existsSync(file)) file = join(dir, 'requirement.md')
  const text = readTextTolerant(file)
  return { stage: name, path: file, text: text ?? '', exists: text !== null }
}

// ------------------------------------------------------- the one write we do

/**
 * Record a decision in `approvals/<stage>.md`, in the v0.2 convention.
 *
 * Three refusals are deliberate:
 *  - an already-decided gate is never overwritten (a stray click must not
 *    reverse an approval the pipeline may already have acted on);
 *  - the file is re-read afterwards, so what we report is what Python will
 *    read, not what we meant to write;
 *  - the reason is folded to one line, because `read_decision` only ever looks
 *    at the rest of the deciding line.
 *
 * An approval may carry a comment: `approved: <문구>` is the conditional
 * approval the engine already understands — it survives `read_decision` as the
 * decision's reason and is handed to every later stage. A bare `approved` is
 * still written when no comment was typed.
 *
 * @param repoRoot  the repository the slice lives in
 * @param input     the slice, stage, decision and the human's one line
 * @flow  already decided -> refuse ; build the decision line (with the comment
 *        when there is one) -> append below the template -> atomic write ->
 *        re-read, and report what Python will see rather than what we meant
 */
export function writeApproval(repoRoot: string, input: ApprovalInput): ApprovalResult {
  const dir = sliceDir(repoRoot, input.sliceId)
  const file = join(dir, 'approvals', `${safeSegment(input.stage)}.md`)

  const existing = readTextTolerant(file)
  const before = existing === null ? PENDING : parseDecision(existing)
  if (before.verdict !== 'pending') {
    return { ok: false, path: file, effective: before.verdict, conflict: before.verdict }
  }

  const comment = oneLine(input.reason ?? '')
  const line =
    input.decision === 'approved'
      ? comment === ''
        ? 'approved'
        : `approved: ${comment}`
      : `rejected: ${comment}`.trimEnd()
  // The template (all comments) is kept: it is the human-readable record of
  // what the gate was, and read_decision skips it anyway.
  const kept = existing === null || existing === '' || existing.endsWith('\n') ? existing : `${existing}\n`
  const body = `${kept ?? ''}${line}\n`.replace(/\r\n/g, '\n')

  try {
    mkdirSync(dirname(file), { recursive: true })
    writeTextAtomic(file, body)
  } catch (err) {
    return { ok: false, path: file, error: String(err) }
  }

  const effective = readDecision(file).verdict
  return { ok: effective === input.decision, path: file, effective }
}

/** `<name>.tmp` then rename — a poller never sees half a decision. */
export function writeTextAtomic(file: string, text: string): void {
  const tmp = `${file}.tmp`
  let last: unknown = null
  for (let attempt = 0; attempt < REPLACE_RETRIES; attempt++) {
    try {
      writeFileSync(tmp, text, { encoding: 'utf8' })
      renameSync(tmp, file)
      return
    } catch (err) {
      // Windows: a reader holding the file makes rename fail until it lets go.
      // Same budget as storage.write_json_atomic — 1 second, then give up.
      last = err
      sleepMs(REPLACE_RETRY_MS)
    }
  }
  throw last
}

// -------------------------------------------------------------------- guards

const SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

/**
 * A slice id and a stage name arrive from the renderer, so neither may become a
 * path. One directory name, no separators, no traversal.
 */
export function safeSegment(value: string): string {
  const text = typeof value === 'string' ? value : ''
  if (!SEGMENT.test(text) || text.includes('..')) {
    throw new Error(`unsafe path segment: ${JSON.stringify(value)}`)
  }
  return text
}

function sliceDir(repoRoot: string, sliceId: string): string {
  const base = resolve(repoRoot, AIDEV_DIRNAME)
  const dir = resolve(base, 'slices', safeSegment(sliceId))
  if (!dir.startsWith(base + sep)) {
    throw new Error(`path escapes ${base}`)
  }
  return dir
}

// ------------------------------------------------------------------- helpers

function listDirNames(root: string): string[] {
  try {
    return readdirSync(root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
  } catch {
    return []
  }
}

function isDirectory(path: string): boolean {
  try {
    return statSync(path).isDirectory()
  } catch {
    return false
  }
}

function byUpdatedDesc(a: { id: string; updatedAt: string | null }, b: typeof a): number {
  const at = Date.parse(a.updatedAt ?? '') || 0
  const bt = Date.parse(b.updatedAt ?? '') || 0
  if (at !== bt) return bt - at
  return a.id < b.id ? 1 : a.id > b.id ? -1 : 0
}

/** Python's `read_decision` starts with `text.lstrip('﻿')`; so do we. */
function stripBom(text: string): string {
  let start = 0
  while (text.charCodeAt(start) === 0xfeff) start += 1
  return start === 0 ? text : text.slice(start)
}

/** The convention is one line, so a pasted paragraph has to become one. */
function oneLine(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function sleepMs(ms: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms)
}

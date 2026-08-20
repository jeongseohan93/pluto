/**
 * IPC contract between the Electron main process and the IDE renderer.
 *
 * v0.0.1: every payload below is served from main-owned mock data. The Python
 * `aidev` core is not wired in yet — only the shape of the boundary is.
 */

import type { CodeViewBridge } from '../domains/code-view/types'
import type { GraphBridge } from '../domains/graph-view/types'
import type {
  PipelineBridge,
  SliceMerge,
  SliceQuota,
  SliceStopped
} from '../domains/pipeline/types'

export type RunState = 'idle' | 'running' | 'awaiting-approval' | 'passed' | 'failed'

export type ApprovalState = 'not-requested' | 'pending' | 'approved' | 'rejected'

export interface ProjectInfo {
  name: string
  root: string
  branch: string
  dirtyFiles: number
}

export interface WorkspaceSummary {
  id: string
  title: string
  slice: string
  agent: string
  state: RunState
  approval: ApprovalState
  changedFiles: number
  testsPassed: number
  testsFailed: number
}

export interface FileNode {
  path: string
  name: string
  kind: 'dir' | 'file'
  change?: 'added' | 'modified'
  children?: FileNode[]
}

export type SymbolKind = 'function' | 'class' | 'test'

export interface GraphSymbol {
  id: string
  name: string
  kind: SymbolKind
  signature: string
  startLine: number
  endLine: number
  changed: boolean
  summary: string
  /** Excerpt shown by the Code surface. Not a file read — authored mock text. */
  code: string
}

/**
 * A file box in the graph.
 *
 * `x` / `y` / `width` are authored, not computed. v0.0.1 deliberately has no
 * layout engine: this mock exists to settle the visual language before v0.0.4
 * builds the real graph, and a renderer that only draws given coordinates is
 * the cheapest way to keep that promise.
 */
export interface GraphFileBox {
  id: string
  path: string
  x: number
  y: number
  width: number
  changed: boolean
  scope: 'focus' | 'related' | 'context'
  symbols: GraphSymbol[]
}

export interface GraphEdge {
  id: string
  from: string
  to: string
  kind: 'calls' | 'tested-by'
}

export interface CodeGraph {
  files: GraphFileBox[]
  edges: GraphEdge[]
  width: number
  height: number
}

export interface DiffLine {
  type: 'context' | 'add' | 'del'
  text: string
}

export interface DiffFile {
  path: string
  change: 'added' | 'modified'
  added: number
  removed: number
  symbols: string[]
  hunkHeader: string
  lines: DiffLine[]
}

export interface ChangeSummary {
  filesChanged: number
  functionsModified: number
  functionsAdded: number
  testsAdded: number
  affected: string[]
  files: DiffFile[]
}

export interface TestCase {
  id: string
  name: string
  file: string
  state: 'passed' | 'failed' | 'skipped'
  durationMs: number
  covers: string[]
}

export interface TelemetrySnapshot {
  agent: string
  model: string
  state: RunState
  sessionUsagePct: number
  contextTokens: number
  contextLimit: number
  turns: number
  inputTokens: number
  cacheCreationTokens: number
  cacheReadTokens: number
  outputTokens: number
  peakContextTokens: number
  costUsd: number
  currentTool: string
  currentTarget: string
  repeatedReads: number
}

export interface LogLine {
  ts: string
  level: 'info' | 'tool' | 'ok' | 'warn' | 'error'
  text: string
}

export interface SessionLogs {
  agent: LogLine[]
  terminal: LogLine[]
  test: LogLine[]
}

// --------------------------------------------------------------------------
// v0.2.5 — the pipeline binding. Everything below is read from a real
// `<repo>/.aidev/` by the main process; none of it is mock.
// --------------------------------------------------------------------------

/** One entry of `state.json`'s `stages`. Keys are open — v0.2 lets a
 *  requirement declare its own stage names, and a reader that only knew
 *  plan/implement/test would silently hide the rest. */
export interface SliceStage {
  name: string
  /** `pending` / `running` / `done` / `failed`, and whatever v0.5 adds. */
  status: string
  approval?: string
  approvalReason?: string
  runId?: string
  attempts?: number
  verdict?: string
}

/**
 * Freshness of the run a slice is inside, under `aidev/reporter.py`'s rule:
 * a `running` live.json that has not moved for 10s is stale. Never derived
 * from state.json — that file only moves at stage boundaries.
 */
export interface LiveStatus {
  status: string
  updatedAt: number
  ageSeconds: number
  stale: boolean
}

export interface SliceState {
  id: string
  dir: string
  /** `pending` / `running:<stage>` / `waiting_approval:<stage>` / `quota_wait`
   *  / `done` / `failed` / `rejected` / `merged` / `discarded`. */
  status: string
  /** The stage whose gate is open, or null. This is what may be approved. */
  waitingStage: string | null
  stages: SliceStage[]
  gates: string[]
  updatedAt: string | null
  reason: string | null
  testVerdict: string | null
  epic: { epic_id?: string; index?: number } | null
  live: LiveStatus | null
  /** `state["merge"]`: written only by a real merge or a real conflict. */
  merge: SliceMerge | null
  /** `state["quota"]`: the usage-limit wait currently open, if any. */
  quota: SliceQuota | null
  /** `state["recovery"]["stopped"]`: which safety pin ended the automation. */
  stopped: SliceStopped | null
  /** state.json was missing or unusable. The slice is still listed. */
  unreadable: boolean
}

export interface EpicSliceRef {
  index: number | null
  title: string
  sliceId: string | null
  status: string
}

export interface EpicState {
  id: string
  status: string
  current: number | null
  slices: EpicSliceRef[]
  updatedAt: string | null
  unreadable: boolean
}

export interface RepoState {
  root: string | null
  name: string | null
  /** `<root>/.aidev` is a directory. False means "probably the wrong folder". */
  isAidevRepo: boolean
  /** Owned by main and stored under `userData` — never written into a repo. */
  recent: string[]
  slices: SliceState[]
  epics: EpicState[]
  readAt: number
}

/** The stage output a human reads before deciding. Plain text, not rendered. */
export interface StageArtifact {
  stage: string
  path: string
  text: string
  exists: boolean
}

export type ApprovalVerdict = 'pending' | 'approved' | 'rejected'
export type ApprovalDecision = 'approved' | 'rejected'

export interface ApprovalInput {
  sliceId: string
  stage: string
  decision: ApprovalDecision
  reason?: string
}

export interface ApprovalResult {
  ok: boolean
  path: string
  /** What Python will read back, checked after the write — not what we meant. */
  effective?: ApprovalVerdict
  /** The gate was already decided; nothing was written. */
  conflict?: ApprovalVerdict
  error?: string
}

/**
 * The full surface the renderer is allowed to reach. Capability-scoped by
 * design — no `exec`, no arbitrary path reads.
 *
 * v0.2.6 composes it from the domains: the pipeline half can run five named
 * CLI invocations (main builds every argv), and the graph half can only read.
 * The renderer still cannot name a command line or a path of its own.
 *
 * v0.2.7 adds the code half, which is the one place the renderer does name a
 * path — and the only one it can, because main answers nothing that this
 * repository's graph did not already put on the screen.
 */
export interface AidevBridge extends PipelineBridge, GraphBridge, CodeViewBridge {
  getProject(): Promise<ProjectInfo>
  getWorkspaces(): Promise<WorkspaceSummary[]>
  getProjectTree(): Promise<FileNode[]>
  getGraph(workspaceId: string): Promise<CodeGraph>
  getChangeSummary(workspaceId: string): Promise<ChangeSummary>
  getTests(workspaceId: string): Promise<TestCase[]>
  getTelemetrySnapshot(): Promise<TelemetrySnapshot>
  getSessionLogs(): Promise<SessionLogs>

  // -- v0.2.5 pipeline binding
  getRepoState(): Promise<RepoState>
  /** Main opens the folder dialog; the renderer never names a path itself. */
  openRepoDialog(): Promise<RepoState>
  /** Only a path already in `recent` is accepted. */
  selectRepo(root: string): Promise<RepoState>
  getStageArtifact(sliceId: string, stage: string): Promise<StageArtifact>
  /**
   * The one and only capability that writes into the target repository:
   * `<repo>/.aidev/slices/<id>/approvals/<stage>.md`. Judgement stays with the
   * Python core — Pluto records the human's answer and nothing else.
   */
  writeApproval(input: ApprovalInput): Promise<ApprovalResult>
}

/** Channel names, kept in one place so main and preload cannot drift apart. */
export const IPC = {
  project: 'aidev:get-project',
  workspaces: 'aidev:get-workspaces',
  projectTree: 'aidev:get-project-tree',
  graph: 'aidev:get-graph',
  changeSummary: 'aidev:get-change-summary',
  tests: 'aidev:get-tests',
  telemetry: 'aidev:get-telemetry-snapshot',
  sessionLogs: 'aidev:get-session-logs',
  repoState: 'aidev:get-repo-state',
  openRepo: 'aidev:open-repo-dialog',
  selectRepo: 'aidev:select-repo',
  artifact: 'aidev:get-stage-artifact',
  approve: 'aidev:write-approval',

  // v0.2.6 — the pipeline's own commands, and the Function DB.
  requirements: 'aidev:get-requirements',
  runCommand: 'aidev:run-command',
  stopCommand: 'aidev:stop-command',
  commandState: 'aidev:get-command-state',
  /** main -> renderer, the only push channel there is. */
  commandEvent: 'aidev:command-event',
  sliceFailure: 'aidev:get-slice-failure',
  graphIndex: 'aidev:get-graph-index',
  graphNode: 'aidev:get-graph-node',

  /** v0.2.7 — one text file out of the open repository, and only one the
   *  graph already knows about. */
  sourceFile: 'aidev:get-source-file'
} as const

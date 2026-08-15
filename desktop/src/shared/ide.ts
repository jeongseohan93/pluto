/**
 * IPC contract between the Electron main process and the IDE renderer.
 *
 * v0.0.1: every payload below is served from main-owned mock data. The Python
 * `aidev` core is not wired in yet — only the shape of the boundary is.
 */

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

/**
 * The full surface the renderer is allowed to reach. Capability-scoped and
 * read-only by design — no `exec`, no arbitrary path reads.
 */
export interface AidevBridge {
  getProject(): Promise<ProjectInfo>
  getWorkspaces(): Promise<WorkspaceSummary[]>
  getProjectTree(): Promise<FileNode[]>
  getGraph(workspaceId: string): Promise<CodeGraph>
  getChangeSummary(workspaceId: string): Promise<ChangeSummary>
  getTests(workspaceId: string): Promise<TestCase[]>
  getTelemetrySnapshot(): Promise<TelemetrySnapshot>
  getSessionLogs(): Promise<SessionLogs>
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
  sessionLogs: 'aidev:get-session-logs'
} as const

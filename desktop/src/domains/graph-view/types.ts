/**
 * The Function DB as the screen needs it.
 *
 * Every field below is a column of `.aidev/graph/graph.db` (see
 * `aidev/graph/db.py` SCHEMA), renamed from snake_case and nothing else. The
 * app is a mirror here — 3조: 화면은 비추기만 한다 — so there is no derived
 * state in this file that the DB could disagree with.
 */

/** The `meta` table, plus the two counts the header shows. */
export interface GraphMeta {
  schema: string
  repo: string
  baseCommit: string
  branch: string
  builtAt: string
  updatedAt: string
  aidevVersion: string
  /** `meta.dirty`: the worktree had uncommitted changes when this was built. */
  dirty: boolean
  /** `.aidev/graph/dirty` exists: code moved since, and no query has paid yet. */
  markedStale: boolean
  files: number
  functions: number
  /** Non-test functions with a summary — the coverage the colours show. */
  specCovered: number
  specTotal: number
}

/** One row of `functions`. */
export interface GraphFunction {
  id: number
  qualname: string
  name: string
  path: string
  lang: string
  lineno: number
  endLineno: number
  signature: string
  kind: string
  /** The spec's first line, empty when the function has no spec. */
  summary: string
  hasSpec: boolean
  isTest: boolean
}

/** One parsed file and the functions defined in it, in coordinate order. */
export interface GraphFileGroup {
  path: string
  lang: string
  functions: GraphFunction[]
}

/**
 * One file→file link: `calls` grouped by the two ends' files.
 *
 * This is the whole-view level of detail. A file pair is the coarsest thing the
 * graph can say that is still true, which is why it is what the canvas draws
 * when nothing is selected — structure without the hairball.
 */
export interface GraphFileEdge {
  from: string
  to: string
  /** How many call sites in `from` resolve into a function of `to`. */
  weight: number
}

/** One resolved call, function to function, deduplicated. */
export interface GraphEdge {
  from: number
  to: number
}

/** Why the graph could not be read. Each has its own thing to say on screen. */
export type GraphProblem = 'no-graph' | 'unreadable' | 'schema' | 'no-sqlite'

export interface GraphIndexResult {
  ok: boolean
  problem?: GraphProblem
  detail?: string
  /** Always filled, even when the read failed — "where is it?" is the answer. */
  dbPath: string
  meta: GraphMeta | null
  files: GraphFileGroup[]
  /** What the whole view draws: per file pair call totals, heaviest first. */
  fileEdges: GraphFileEdge[]
  /** Adjacency's raw material, for deciding a hover's neighbours in memory.
   *  Not a render list — the canvas never draws all of these at once. */
  edges: GraphEdge[]
}

/** One row of `tags`: `@param`, `@flow`, and whatever else a spec carried. */
export interface GraphTag {
  tag: string
  value: string
  core: boolean
  lineno: number
}

/** One row of `calls_of`: what this function calls. */
export interface GraphCallRow {
  callLine: number
  callee: string
  raw: string
  targetId: number | null
  targetPath: string | null
  targetLine: number | null
  targetQualname: string | null
}

/** One row of `callers`: a call site that names this function. */
export interface GraphCallerRow {
  callerId: number
  path: string
  caller: string
  callerLine: number
  callLine: number
  /** The engine refused to pick a target (two functions share the name). */
  unresolved: boolean
}

export interface GraphNodeDetail {
  fn: GraphFunction
  tags: GraphTag[]
  calls: GraphCallRow[]
  callers: GraphCallerRow[]
  /** How many caller rows were cut — a long list must not stall the panel. */
  callersTruncated: number
}

/** The graph half of the renderer's capability surface. Read-only, always. */
export interface GraphBridge {
  getGraphIndex(): Promise<GraphIndexResult>
  getGraphNode(id: number): Promise<GraphNodeDetail | null>
}

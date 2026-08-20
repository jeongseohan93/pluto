/**
 * The Function DB as the screen needs it.
 *
 * Every field below is a column of `.aidev/graph/graph.db` (see
 * `aidev/graph/db.py` SCHEMA), renamed from snake_case and nothing else. The
 * app is a mirror here — 3조: 화면은 비추기만 한다 — so there is no derived
 * state in this file that the DB could disagree with.
 *
 * The trace section at the bottom is the one place this file carries runtime
 * values as well as types: the depth range is part of the bridge's argument, and
 * main must not take the renderer's word for it — so both sides read the same
 * three constants and the same clamp.
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

// ------------------------------------------------------------------- trace
//
// One function's call chain, followed N rings out — and, just as importantly,
// the places the chain could not be followed. `readGraphIndex`'s `edges` are
// already filtered by `JOIN functions dst`, so the unresolved call sites never
// reach the renderer at all; a chain computed in memory could therefore never
// say "it stops here". That is why a trace is its own query rather than a BFS
// over the adjacency the surface already holds.

/** 어느 쪽으로 따라가는가. calls = 이 함수가 부르는 쪽, callers = 이 함수를 부르는 쪽. */
export type TraceDirection = 'calls' | 'callers'

/** UI가 조절할 수 있는 범위와 기본값. main도 이 상수로 클램프한다. */
export const TRACE_DEPTH_MIN = 1
export const TRACE_DEPTH_MAX = 5
export const TRACE_DEPTH_DEFAULT = 3

/**
 * 요청받은 깊이를, 실제로 답할 수 있는 깊이로.
 *
 * 렌더러가 보낸 숫자는 main에서도 다시 통과한다 — 경계를 넘어온 값은 모양이
 * 맞는다고 우리 것이 아니다(`register-handlers.ts` 머리말의 두 번째 규칙). 두
 * 곳이 각자 판단하면 언젠가 어긋나므로, 판단은 이 함수 하나에만 둔다.
 *
 * @param depth  요청된 깊이. 숫자가 아니면 기본값으로 읽는다
 * @flow  숫자가 아니면 기본값 -> 정수로 반올림 -> 1..5로 자른다
 */
export function clampTraceDepth(depth: number): number {
  if (!Number.isFinite(depth)) return TRACE_DEPTH_DEFAULT
  const whole = Math.round(depth)
  return Math.min(TRACE_DEPTH_MAX, Math.max(TRACE_DEPTH_MIN, whole))
}

/** 사슬 위의 함수 하나. */
export interface TraceNode {
  id: number
  /** 루트에서 몇 걸음인가. 0은 루트 자신, 가장 짧은 경로 기준. */
  depth: number
}

/** 사슬이 실제로 따라간 호출 하나. 화살표 방향은 언제나 caller -> callee. */
export interface TraceEdge {
  from: number
  to: number
  /** 이 걸음이 도달한 쪽의 깊이. 1이 루트의 첫 고리. */
  depth: number
}

/**
 * 추적이 끊긴 자리. 그래프가 그 이름을 알지만 어느 것인지 고르기를 거부했다.
 */
export interface TraceBreak {
  /** 이 끊김이 매달린 사슬 노드. */
  atId: number
  depth: number
  /** 따라갈 수 없었던 이름. callers 방향에서는 사슬 노드 자신의 이름이다. */
  callee: string
  /** 그런 호출부가 몇 군데인가. */
  count: number
  /** 그 이름을 가진 함수가 저장소에 몇 개인가 — 왜 모호한지의 답. */
  candidates: number
}

export interface GraphTrace {
  rootId: number
  direction: TraceDirection
  /** 실제로 쓰인 깊이(클램프 뒤). */
  depth: number
  nodes: TraceNode[]
  edges: TraceEdge[]
  breaks: TraceBreak[]
  /** 그래프가 이름조차 모르는 호출부 — 끊김이 아니라 저장소의 경계. 개수만. */
  external: number
  /** 노드 상한에 걸려 잘린 노드 수. 범례가 이 숫자를 인쇄한다. */
  truncated: number
}

/** What a trace is asked for: a root, a direction, and how far to follow. */
export interface TraceRequest {
  id: number
  direction: TraceDirection
  depth: number
}

/** The graph half of the renderer's capability surface. Read-only, always. */
export interface GraphBridge {
  getGraphIndex(): Promise<GraphIndexResult>
  getGraphNode(id: number): Promise<GraphNodeDetail | null>
  /** 한 함수에서 뻗어나가는 호출 사슬. 저장소·DB·id 중 하나라도 없으면 null.
   *  아무것도 해석되지 않은 함수는 루트뿐인 *정상* 결과이지 null이 아니다. */
  getGraphTrace(request: TraceRequest): Promise<GraphTrace | null>
}

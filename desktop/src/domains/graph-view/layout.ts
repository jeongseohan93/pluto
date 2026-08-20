/**
 * Where every file box and every function row goes — and nothing about React.
 *
 * There is no layout *engine* here and that is the design, not a shortcut. The
 * LOD rule is what removes the need for one: the whole view draws file→file
 * totals (a few hundred curves), and function edges appear only around the
 * selection. With at most a few hundred edges on screen there is nothing for a
 * force simulation to untangle, so the placement can be the one thing a reader
 * can predict — files in path order, packed into columns. That is also why this
 * slice adds no rendering dependency: d3-force, cytoscape and reactflow all
 * exist to survive 8669 edges at once, and drawing 8669 edges at once is
 * precisely what we refuse to do.
 *
 * Pure and deterministic, so `layout.test.ts` can assert that 78 boxes do not
 * overlap without a browser.
 */
import type { GraphEdge, GraphFileEdge, GraphFileGroup, GraphFunction } from './types'

export const BOX_W = 268
export const HEADER_H = 30
export const ROW_H = 18
export const PAD_B = 8
export const GAP_X = 28
export const GAP_Y = 18
/** How tall a column may grow before the next box starts a new one. */
export const COLUMN_H = 1400

/** How many file links the whole view draws. Past this the legend says so. */
export const MAX_FILE_EDGES = 240
/** The thinnest a file link may be drawn: a weight of 1 still has to be seen. */
export const EDGE_W_MIN = 0.5
/** The thickest. Wider than this and neighbouring links start to touch. */
export const EDGE_W_MAX = 4
/** How many neighbours one hover may light up. `say` has 150 callers. */
export const HOVER_MARK_LIMIT = 300
/** How far right a routed-around curve may bulge before it invades the next column. */
export const LANE_MAX = 160

export interface GraphBox {
  path: string
  lang: string
  x: number
  y: number
  width: number
  height: number
  functions: GraphFunction[]
}

/** Where an edge may attach to one function row. */
export interface EdgeAnchor {
  left: number
  right: number
  y: number
}

export interface Anchor extends EdgeAnchor {
  /** Which box holds this row, so a selection can re-render only its box. */
  boxIndex: number
}

export interface GraphLayout {
  boxes: GraphBox[]
  anchors: Map<number, Anchor>
  /** path -> index into `boxes`, so a file link can find both of its ends. */
  boxOf: Map<string, number>
  width: number
  height: number
}

/**
 * How tall a file box is: header, one row per function, a little padding.
 *
 * @param count  how many functions the file defines
 */
export function boxHeight(count: number): number {
  return HEADER_H + count * ROW_H + PAD_B
}

/**
 * Place every file box and every function row, in path order.
 *
 * @param files  the file groups, already in the order they should be read
 * @flow  fill a column until the next box would pass COLUMN_H -> start another
 *        -> record one anchor per function and one per file -> the canvas is
 *        the outer bound
 * 주요 내부 변수: x/y(현재 열의 좌상단), tallest(캔버스 높이)
 */
export function layoutGraph(files: GraphFileGroup[]): GraphLayout {
  const boxes: GraphBox[] = []
  const anchors = new Map<number, Anchor>()
  const boxOf = new Map<string, number>()
  let x = 0
  let y = 0
  let tallest = 0

  for (const file of files) {
    const height = boxHeight(file.functions.length)
    // A box taller than a whole column still gets its own column rather than
    // being cut: pipeline.py is one file with 207 functions, and hiding half of
    // it would be the graph lying about the repository.
    if (y > 0 && y + height > COLUMN_H) {
      x += BOX_W + GAP_X
      y = 0
    }
    const box: GraphBox = {
      path: file.path,
      lang: file.lang,
      x,
      y,
      width: BOX_W,
      height,
      functions: file.functions
    }
    const boxIndex = boxes.length
    boxes.push(box)
    boxOf.set(file.path, boxIndex)

    file.functions.forEach((fn, row) => {
      anchors.set(fn.id, {
        left: x,
        right: x + BOX_W,
        y: y + HEADER_H + row * ROW_H + ROW_H / 2,
        boxIndex
      })
    })

    y += height + GAP_Y
    if (y > tallest) tallest = y
  }

  return {
    boxes,
    anchors,
    boxOf,
    width: boxes.length === 0 ? 0 : x + BOX_W,
    height: Math.max(0, tallest - GAP_Y)
  }
}

/**
 * The curve one edge is drawn as.
 *
 * Moved here from `GraphSurface.tsx` so the demo graph and the Function DB draw
 * the same line — two copies of one curve is two curves the first time one of
 * them is tuned.
 *
 * @param from  the source row's anchor
 * @param to    the target row's anchor
 * @flow  a target that is not to the right routes around the right-hand side ;
 *        otherwise a plain left-to-right bezier
 */
export function edgePath(from: EdgeAnchor, to: EdgeAnchor): string {
  const x1 = from.right
  const x2 = to.left

  // Calls between symbols of the same file (and any other target that is not to
  // the right of its source) route around the right-hand side, so the arrow
  // still lands on the target instead of cutting back across the box.
  if (x2 <= x1) {
    // Clamped: pipeline.py is 3700px tall, and an unbounded lane would swing
    // the curve 800px right — straight through whatever is in the next column.
    const lane = x1 + 16 + Math.min(LANE_MAX, Math.abs(to.y - from.y) * 0.22)
    return `M ${x1} ${from.y} C ${lane} ${from.y}, ${lane} ${to.y}, ${to.right} ${to.y}`
  }

  const dx = Math.max(28, (x2 - x1) * 0.45)
  return `M ${x1} ${from.y} C ${x1 + dx} ${from.y}, ${x2 - dx} ${to.y}, ${x2} ${to.y}`
}

// -------------------------------------------------------------- file links
//
// The whole view's level of detail. One curve per file pair instead of one per
// call: 8669 edges become a few hundred, and "which files talk to which" is a
// question the picture can actually answer.

/**
 * Where a file link attaches to a box — the middle of its two vertical sides.
 *
 * @param box  the placed box
 */
export function boxAnchor(box: GraphBox): EdgeAnchor {
  return { left: box.x, right: box.x + box.width, y: box.y + box.height / 2 }
}

/** One file→file curve, ready to draw. */
export interface FileLine {
  key: string
  d: string
  from: string
  to: string
  weight: number
  width: number
  fromBox: number
  toBox: number
}

export interface FileLines {
  lines: FileLine[]
  /** How many were drawn, and how many there were. The legend prints both, so
   *  a cut list never reads as the whole truth. */
  shown: number
  total: number
}

/**
 * The file links the whole view draws, heaviest first and capped.
 *
 * @param edges   every file pair the index counted
 * @param layout  the placed boxes, for both ends of each link
 * @param limit   how many links to draw at most
 * @flow  drop pairs whose ends are not on this canvas (a half-written DB names
 *        files that have no box) -> heaviest first -> keep `limit` of them ->
 *        one curve each, its width scaled against the heaviest kept
 * 주요 내부 변수: usable(양끝이 캔버스에 있는 쌍), heaviest(굵기의 기준)
 */
export function fileLines(
  edges: GraphFileEdge[],
  layout: GraphLayout,
  limit = MAX_FILE_EDGES
): FileLines {
  const usable = edges.filter(
    (edge) => layout.boxOf.has(edge.from) && layout.boxOf.has(edge.to) && edge.from !== edge.to
  )
  const ordered = usable.slice().sort((a, b) => b.weight - a.weight)
  const kept = ordered.slice(0, Math.max(0, limit))
  const heaviest = kept.length === 0 ? 0 : kept[0].weight

  const lines = kept.map((edge) => {
    const fromBox = layout.boxOf.get(edge.from) as number
    const toBox = layout.boxOf.get(edge.to) as number
    return {
      key: `${edge.from}->${edge.to}`,
      d: edgePath(boxAnchor(layout.boxes[fromBox]), boxAnchor(layout.boxes[toBox])),
      from: edge.from,
      to: edge.to,
      weight: edge.weight,
      width: edgeWidth(edge.weight, heaviest),
      fromBox,
      toBox
    }
  })

  return { lines, shown: lines.length, total: usable.length }
}

/**
 * How thick one file link is drawn: its weight against the heaviest one.
 *
 * Square root rather than linear, so a pair with 300 calls and a pair with 100
 * are still told apart instead of both sitting flat against the maximum.
 *
 * @param weight    this link's call count
 * @param heaviest  the heaviest weight on screen
 * @flow  nothing to compare against -> the minimum ; else scale and clamp
 */
export function edgeWidth(weight: number, heaviest: number): number {
  if (!(heaviest > 1) || !(weight > 0)) return EDGE_W_MIN
  const ratio = Math.min(1, weight / heaviest)
  const width = EDGE_W_MIN + (EDGE_W_MAX - EDGE_W_MIN) * Math.sqrt(ratio)
  return Math.min(EDGE_W_MAX, Math.max(EDGE_W_MIN, width))
}

// ------------------------------------------------------------------- marks
//
// What a row looks like right now. Selection *draws* edges; hover only lights
// rows up — which is why a hover can afford to touch 150 neighbours and a
// mouse crossing the canvas never makes a single path appear or disappear.

export interface Adjacency {
  outs: Map<number, number[]>
  ins: Map<number, number[]>
}

/**
 * Both directions of the call graph, as two lookups.
 *
 * Built once from the index so a hover is a Map read rather than a query: at
 * 1435 rows, one IPC round trip per row the mouse crosses is the lag.
 *
 * @param edges  the deduplicated function-to-function calls
 * @flow  each edge lands in the caller's outs and the callee's ins
 */
export function buildAdjacency(edges: GraphEdge[]): Adjacency {
  const outs = new Map<number, number[]>()
  const ins = new Map<number, number[]>()
  for (const edge of edges) {
    push(outs, edge.from, edge.to)
    push(ins, edge.to, edge.from)
  }
  return { outs, ins }
}

/**
 * Append to a bucket, starting one where there is none yet.
 *
 * @param map    the buckets
 * @param key    which bucket
 * @param value  what to put in it
 */
function push(map: Map<number, number[]>, key: number, value: number): void {
  const bucket = map.get(key)
  if (bucket) bucket.push(value)
  else map.set(key, [value])
}

/** How one row is drawn: what it is to whatever the human is pointing at. */
export type RowMark = 'selected' | 'calls' | 'callers' | 'both' | 'hover' | 'near'

const MARK_RANK: Record<RowMark, number> = {
  selected: 5,
  both: 4,
  calls: 3,
  callers: 3,
  hover: 2,
  near: 1
}

/**
 * One function's direct neighbours, each marked by which way the call goes.
 *
 * @param adj    both directions of the call graph
 * @param id     the function being pointed at
 * @param limit  how many neighbours to mark before giving up
 * @flow  the node itself is 'hover' -> callees are 'calls' -> callers are
 *        'callers', or 'both' where they are already a callee -> stop at limit
 * 주요 내부 변수: marks(결과), room(아직 표시할 수 있는 이웃 수)
 */
export function neighbourMarks(
  adj: Adjacency,
  id: number,
  limit = HOVER_MARK_LIMIT
): Map<number, RowMark> {
  const marks = new Map<number, RowMark>()
  marks.set(id, 'hover')
  let room = Math.max(0, limit)

  for (const to of adj.outs.get(id) ?? []) {
    if (room === 0) break
    if (to === id || marks.has(to)) continue
    marks.set(to, 'calls')
    room -= 1
  }
  for (const from of adj.ins.get(id) ?? []) {
    if (from === id) continue
    const already = marks.get(from)
    if (already === 'calls') {
      marks.set(from, 'both')
      continue
    }
    if (already || room === 0) continue
    marks.set(from, 'callers')
    room -= 1
  }
  return marks
}

/**
 * Two sets of marks in one, the stronger meaning winning each row.
 *
 * @param base   the marks that were there
 * @param extra  the marks to lay over them
 * @flow  a row in both: calls meeting callers becomes 'both', otherwise the
 *        higher-ranked mark stays. Neither argument is modified.
 */
export function mergeMarks(
  base: Map<number, RowMark>,
  extra: Map<number, RowMark>
): Map<number, RowMark> {
  const out = new Map<number, RowMark>(base)
  for (const [id, mark] of extra) {
    const had = out.get(id)
    if (!had) {
      out.set(id, mark)
      continue
    }
    if ((had === 'calls' && mark === 'callers') || (had === 'callers' && mark === 'calls')) {
      out.set(id, 'both')
      continue
    }
    if (MARK_RANK[mark] > MARK_RANK[had]) out.set(id, mark)
  }
  return out
}

/**
 * The marks folded by box, so each box is handed only its own rows.
 *
 * A box with nothing to show gets no key at all — the caller can then pass one
 * shared empty map and `memo` will skip that box entirely. That is what keeps
 * 105 boxes still while one of them lights up.
 *
 * @param anchors  where every row sits, including which box holds it
 * @param marks    every marked row
 * @flow  each mark joins its box's map ; unknown ids are skipped
 */
export function groupMarks(
  anchors: Map<number, Anchor>,
  marks: Map<number, RowMark>
): Map<number, Map<number, RowMark>> {
  const out = new Map<number, Map<number, RowMark>>()
  for (const [id, mark] of marks) {
    const anchor = anchors.get(id)
    if (!anchor) continue
    const bucket = out.get(anchor.boxIndex)
    if (bucket) bucket.set(id, mark)
    else out.set(anchor.boxIndex, new Map([[id, mark]]))
  }
  return out
}

/**
 * Functions whose name or qualname contains the query, best match first.
 *
 * Ranking is by how the match reads, not by a score: an exact name, then a name
 * that starts with it, then a name that contains it, then the qualname. Ties
 * keep the DB's own order (path, then line), so the same query always lists the
 * same rows in the same places.
 *
 * @param functions  every function in the index
 * @param query      what the human typed
 * @param limit      how many rows the dropdown will show
 * @flow  blank query -> nothing ; score each name ; keep the scored ones ;
 *        stable sort by score then by the order they arrived
 */
export function matchFunctions(
  functions: GraphFunction[],
  query: string,
  limit = 40
): GraphFunction[] {
  const needle = String(query ?? '')
    .trim()
    .toLowerCase()
  if (needle === '') return []

  const scored: { fn: GraphFunction; score: number; order: number }[] = []
  functions.forEach((fn, order) => {
    const name = fn.name.toLowerCase()
    const qual = fn.qualname.toLowerCase()
    let score = -1
    if (name === needle) score = 0
    else if (name.startsWith(needle)) score = 1
    else if (name.includes(needle)) score = 2
    else if (qual.includes(needle)) score = 3
    else if (fn.path.toLowerCase().includes(needle)) score = 4
    if (score >= 0) scored.push({ fn, score, order })
  })

  scored.sort((a, b) => (a.score !== b.score ? a.score - b.score : a.order - b.order))
  return scored.slice(0, limit).map((entry) => entry.fn)
}

/**
 * Every function of the index in one flat list, in the order the boxes read.
 *
 * @param files  the file groups from the index
 */
export function flattenFunctions(files: GraphFileGroup[]): GraphFunction[] {
  const out: GraphFunction[] = []
  for (const file of files) for (const fn of file.functions) out.push(fn)
  return out
}

/**
 * Where every file box and every function row goes — and nothing about React.
 *
 * There is no layout *engine* here and that is the design, not a shortcut. The
 * requirement's LOD rule (only the selected node's neighbours get edges) is
 * what removes the need for one: with at most a few dozen edges on screen there
 * is nothing for a force simulation to untangle, so the placement can be the
 * one thing a reader can predict — files in path order, packed into columns.
 * That is also why this slice adds no rendering dependency: d3-force, cytoscape
 * and reactflow all exist to survive 8669 edges at once, and drawing 8669 edges
 * at once is precisely what we refuse to do.
 *
 * Pure and deterministic, so `layout.test.ts` can assert that 78 boxes do not
 * overlap without a browser.
 */
import type { GraphFileGroup, GraphFunction } from './types'

export const BOX_W = 268
export const HEADER_H = 30
export const ROW_H = 18
export const PAD_B = 8
export const GAP_X = 28
export const GAP_Y = 18
/** How tall a column may grow before the next box starts a new one. */
export const COLUMN_H = 1400

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
 *        -> record one anchor per function -> the canvas is the outer bound
 * 주요 내부 변수: x/y(현재 열의 좌상단), tallest(캔버스 높이)
 */
export function layoutGraph(files: GraphFileGroup[]): GraphLayout {
  const boxes: GraphBox[] = []
  const anchors = new Map<number, Anchor>()
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
    const lane = x1 + 16 + Math.abs(to.y - from.y) * 0.22
    return `M ${x1} ${from.y} C ${lane} ${from.y}, ${lane} ${to.y}, ${to.right} ${to.y}`
  }

  const dx = Math.max(28, (x2 - x1) * 0.45)
  return `M ${x1} ${from.y} C ${x1 + dx} ${from.y}, ${x2 - dx} ${to.y}, ${x2} ${to.y}`
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

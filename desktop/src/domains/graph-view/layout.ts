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
 * The zoom decides which of three levels is placed — file boxes, key rows, or
 * every row — so the placement shrinks with the detail instead of leaving empty
 * rectangles behind. `lodFor` and the thresholds around it are the scale bar.
 *
 * Pure and deterministic, so `layout.test.ts` can assert that 78 boxes do not
 * overlap without a browser.
 */
import type {
  GraphEdge,
  GraphFileEdge,
  GraphFileGroup,
  GraphFunction,
  GraphTrace
} from './types'

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

// ----------------------------------------------------------- scale of detail
//
// A map with no scale bar is unreadable at 1435 functions — that is measured,
// not feared. So the zoom decides *what* is drawn and not merely how big it is
// drawn, and the placement follows: at the file level a box is 72 units tall
// instead of 400, which is what turns a 6800px canvas into one that fits on a
// screen. Every threshold below is a constant on purpose: tuning this after
// looking at a real repository must be three numbers, not a refactor.

/** Which level of detail a zoom draws. */
export type Lod = 'file' | 'key' | 'full'

/** At or below this zoom: file boxes only, no function rows and no function
 *  edges. 105 boxes at FILE_BOX_H pack into 2044x1400 user units, which at 0.55
 *  is 1124x770 client pixels — one screen. */
export const LOD_FILE_MAX = 0.55
/** At or below this zoom: file boxes plus each file's most-called functions. */
export const LOD_KEY_MAX = 0.85
/** How many function rows one file shows at the middle level. */
export const KEY_FN_PER_FILE = 6
/** How tall a file box is when it holds no rows — two lines of header. */
export const FILE_BOX_H = 72
/** The size the file level's two header lines keep *on screen*, in client
 *  pixels. In user units they are these divided by the zoom, which is the only
 *  way a label stays legible while the canvas shrinks under it. */
export const FILE_LABEL_PX = 12
export const FILE_META_PX = 9
/** The zoom a jump to one function lands at: the level that draws every row. */
export const ZOOM_DETAIL = 1
/** The rungs the zoom buttons climb. The bottom two are this slice's far view. */
export const ZOOM_STEPS: readonly number[] = [0.4, 0.5, 0.7, 0.85, 1, 1.25, 1.5, 1.8]
/** The ladder's two ends. The wheel moves between the rungs rather than along
 *  them, so it needs the range and not the rungs; `nearestStep` then puts the
 *  buttons back on the ladder from wherever the wheel left the zoom. */
export const ZOOM_MIN = ZOOM_STEPS[0]
export const ZOOM_MAX = ZOOM_STEPS[ZOOM_STEPS.length - 1]
/** One wheel notch's share of an e-fold. 100px ⇒ ×1.16 — a notch you feel once. */
export const ZOOM_WHEEL_RATE = 0.0015
/** `deltaMode` 1 and 2 are lines and pages; these are what they are worth in px. */
export const WHEEL_LINE_PX = 16
export const WHEEL_PAGE_PX = 400

/** How big the minimap may get, in client pixels. */
export const MINIMAP_W = 196
export const MINIMAP_H = 132

/** No rows at all. One shared instance, so `memo` is not woken by it. */
const NO_ROWS: GraphFunction[] = []
/** No caller counted yet — the default for a layout built without an index. */
const NO_COUNTS: Map<number, number> = new Map()

export interface GraphBox {
  path: string
  lang: string
  x: number
  y: number
  width: number
  height: number
  /** The rows this level actually draws. Empty at the file level. */
  functions: GraphFunction[]
  /** Every function the file defines, drawn or not — the header's "N fn". */
  total: number
  /** Spec coverage: the non-test functions, and how many of them carry a spec. */
  specTotal: number
  specCovered: number
  /** How many rows this level leaves out. Above zero, a "+N more" line is added. */
  hidden: number
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
 * What this zoom draws. The boundaries are inclusive.
 *
 * @param zoom  the surface's zoom factor
 * @flow  anything that is not a positive number reads as the coarsest level
 *        rather than throwing a canvas away
 */
export function lodFor(zoom: number): Lod {
  if (!(zoom > 0)) return 'file'
  if (zoom <= LOD_FILE_MAX) return 'file'
  if (zoom <= LOD_KEY_MAX) return 'key'
  return 'full'
}

/**
 * Which rung of the ladder a zoom is standing on, or the nearest one to it.
 *
 * @param zoom  the current zoom, which need not be a rung at all
 * @flow  the smallest absolute gap wins; ties keep the lower rung
 */
function nearestStep(zoom: number): number {
  let at = 0
  let best = Number.POSITIVE_INFINITY
  for (let i = 0; i < ZOOM_STEPS.length; i++) {
    const gap = Math.abs(ZOOM_STEPS[i] - zoom)
    if (gap < best) {
      best = gap
      at = i
    }
  }
  return at
}

/**
 * One rung closer, or this one again at the top.
 *
 * @param zoom  the current zoom
 */
export function zoomIn(zoom: number): number {
  return ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, nearestStep(zoom) + 1)]
}

/**
 * One rung further away, or this one again at the bottom.
 *
 * @param zoom  the current zoom
 */
export function zoomOut(zoom: number): number {
  return ZOOM_STEPS[Math.max(0, nearestStep(zoom) - 1)]
}

/**
 * Where one wheel event lands the zoom.
 *
 * Exponential rather than additive, so a notch is the same *proportion* at 0.4
 * as it is at 1.8 — otherwise the far view crawls and the close one leaps. The
 * result is continuous and sits between the rungs; the buttons cope, because
 * `nearestStep` never assumed the zoom was standing on one.
 *
 * @param zoom       the current zoom
 * @param deltaY     the wheel's vertical delta, in whatever `deltaMode` says
 * @param deltaMode  0 pixels, 1 lines, 2 pages — the browser's choice, not ours
 * @flow  a delta that is not a number leaves the zoom where it was, rather than
 *        turning the whole canvas into a NaN ; clamped to the ladder's two ends
 */
export function zoomBy(zoom: number, deltaY: number, deltaMode = 0): number {
  const from = zoom > 0 ? zoom : 1
  const at = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, from))
  if (!Number.isFinite(deltaY)) return at
  const px = deltaY * (deltaMode === 1 ? WHEEL_LINE_PX : deltaMode === 2 ? WHEEL_PAGE_PX : 1)
  // Down (a positive delta) is away, which is what a wheel means everywhere else.
  const next = at * Math.exp(-px * ZOOM_WHEEL_RATE)
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next))
}

/**
 * How many distinct functions call each function — its in-degree.
 *
 * `index.edges` is already deduplicated caller→callee, so counting the targets
 * is the whole answer. This is the middle level's idea of "a key function":
 * the DB has no export column (see `aidev/graph/model.py`), and "who is called
 * most" is in any case a more honest way to pick a file's representatives.
 *
 * @param edges  the deduplicated function-to-function calls
 * @flow  one pass over the edges, counting each target
 */
export function callerCounts(edges: GraphEdge[]): Map<number, number> {
  const counts = new Map<number, number>()
  for (const edge of edges) counts.set(edge.to, (counts.get(edge.to) ?? 0) + 1)
  return counts
}

/**
 * The rows this level draws for one file.
 *
 * A level that shows everything hands back the very array it was given, and the
 * file level hands back one shared empty array. That referential identity is
 * not a nicety: it is what lets `FileBox`'s memo skip a hundred boxes.
 *
 * @param functions  the file's functions, in coordinate order
 * @param lod        what this zoom draws
 * @param counts     each function's in-degree, from `callerCounts`
 * @param perFile    how many rows the middle level keeps
 * @flow  file level -> nothing ; full level or a short file -> the same array ;
 *        otherwise the most-called `perFile`, put back in coordinate order so a
 *        row never moves for a reason the reader cannot see
 * 주요 내부 변수: ranked(순위 매기려고 감싼 것), kept(살아남은 행)
 */
export function visibleRows(
  functions: GraphFunction[],
  lod: Lod,
  counts: Map<number, number>,
  perFile = KEY_FN_PER_FILE
): GraphFunction[] {
  if (lod === 'file') return NO_ROWS
  if (lod === 'full' || functions.length <= perFile) return functions

  const ranked = functions.map((fn, order) => ({ fn, order, weight: counts.get(fn.id) ?? 0 }))
  ranked.sort((a, b) => (a.weight !== b.weight ? b.weight - a.weight : a.order - b.order))
  const kept = ranked.slice(0, Math.max(0, perFile))
  kept.sort((a, b) => a.order - b.order)
  return kept.map((entry) => entry.fn)
}

/**
 * How many monospace characters of this size fit across this width.
 *
 * @param width     the space there is, in the same units as `fontSize`
 * @param fontSize  the size the text is drawn at
 * @flow  never below four: a name cut to nothing says less than a cut name
 */
export function fitChars(width: number, fontSize: number): number {
  if (!(fontSize > 0)) return 4
  // 0.62 is Cascadia Mono / Consolas' advance-to-size ratio, measured.
  return Math.max(4, Math.floor((width - 16) / (fontSize * 0.62)))
}

/**
 * How tall a file box is: header, one row per drawn function, a little padding.
 *
 * @param count   how many rows this level actually draws
 * @param lod     what this zoom draws
 * @param hidden  how many rows this level leaves out, for the "+N more" line
 * @flow  the file level is a fixed two-line card ; otherwise the rows decide,
 *        plus one more line where something was left out
 */
export function boxHeight(count: number, lod: Lod = 'full', hidden = 0): number {
  if (lod === 'file') return FILE_BOX_H
  return HEADER_H + count * ROW_H + (hidden > 0 ? ROW_H : 0) + PAD_B
}

/**
 * Place every file box and every function row, in path order.
 *
 * The level of detail is an *input* here rather than a condition inside a
 * component, and that is the whole design: hiding rows while keeping the boxes
 * 400 units tall would leave a hundred empty rectangles scattered over 6800
 * pixels, which is no more readable than the hairball. Because the anchors, the
 * canvas size, the minimap and the file links all read off this one result,
 * changing the level in one place keeps every one of them agreeing.
 *
 * @param files   the file groups, already in the order they should be read
 * @param lod     what this zoom draws
 * @param counts  each function's in-degree, for picking a file's key rows
 * @flow  fill a column until the next box would pass COLUMN_H -> start another
 *        -> record one anchor per *drawn* row and one box per file -> the canvas
 *        is the outer bound
 * 주요 내부 변수: x/y(현재 열의 좌상단), rows(이 레벨이 그리는 행), tallest(캔버스 높이)
 */
export function layoutGraph(
  files: GraphFileGroup[],
  lod: Lod = 'full',
  counts: Map<number, number> = NO_COUNTS
): GraphLayout {
  const boxes: GraphBox[] = []
  const anchors = new Map<number, Anchor>()
  const boxOf = new Map<string, number>()
  let x = 0
  let y = 0
  let tallest = 0

  for (const file of files) {
    const rows = visibleRows(file.functions, lod, counts)
    const hidden = file.functions.length - rows.length
    const height = boxHeight(rows.length, lod, hidden)
    // A box taller than a whole column still gets its own column rather than
    // being cut: pipeline.py is one file with 207 functions, and hiding half of
    // it would be the graph lying about the repository.
    if (y > 0 && y + height > COLUMN_H) {
      x += BOX_W + GAP_X
      y = 0
    }
    let specTotal = 0
    let specCovered = 0
    for (const fn of file.functions) {
      if (fn.isTest) continue
      specTotal += 1
      if (fn.hasSpec) specCovered += 1
    }
    const box: GraphBox = {
      path: file.path,
      lang: file.lang,
      x,
      y,
      width: BOX_W,
      height,
      functions: rows,
      total: file.functions.length,
      specTotal,
      specCovered,
      hidden
    }
    const boxIndex = boxes.length
    boxes.push(box)
    boxOf.set(file.path, boxIndex)

    rows.forEach((fn, row) => {
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

// ----------------------------------------------------------------- dragging
//
// Where the human has pulled a box, as one thin layer over the placement rather
// than a rewrite of it. `layoutGraph`'s result stays byte-identical while a box
// is dragged, which is the whole point: the boxes keep their identity, so
// `FileBox`'s memo survives a drag and only the curves touching a moved box are
// recomputed. Nothing here is written anywhere — these offsets live exactly as
// long as the layout they sit on, so a rebuild is also the way back.

/** How far a pointer may travel and still be a click, in client pixels. */
export const DRAG_SLOP = 3

/** How far one box has been pulled from where it was placed. */
export interface Offset {
  dx: number
  dy: number
}

/** path -> offset. No key means "wherever the layout put it". */
export type Offsets = ReadonlyMap<string, Offset>

/** Nothing dragged yet. One shared instance, so `memo` is not woken by it. */
export const NO_OFFSETS: Offsets = new Map<string, Offset>()

/** The answer for every box nobody has touched — shared, never allocated. */
const ZERO: Offset = { dx: 0, dy: 0 }

/**
 * Has the pointer travelled far enough that this is a drag and not a click?
 *
 * @param dx    how far it has moved across, in client pixels
 * @param dy    how far it has moved down
 * @param slop  how much a click is still allowed to wander
 */
export function isDrag(dx: number, dy: number, slop = DRAG_SLOP): boolean {
  return Math.abs(dx) > slop || Math.abs(dy) > slop
}

/**
 * Where this file's box has been pulled to, or the origin when it has not.
 *
 * @param offsets  every box that has been moved
 * @param path     the file being asked about
 */
export function offsetOf(offsets: Offsets, path: string): Offset {
  return offsets.get(path) ?? ZERO
}

/**
 * The same offsets with one path moved: a new map, the old one untouched.
 *
 * @param offsets  what is moved now
 * @param path     the file being dragged
 * @param offset   where it has got to
 */
export function withOffset(offsets: Offsets, path: string, offset: Offset): Offsets {
  const next = new Map(offsets)
  next.set(path, offset)
  return next
}

/**
 * An offset that cannot push its box off the top or the left of the canvas.
 *
 * The viewBox starts at 0,0 and anything before that is cut, so a box dragged
 * far enough up and left would vanish rather than move. Right and down are not
 * limited at all — `canvasSize` grows the canvas to hold them.
 *
 * @param box     the box as it was placed
 * @param offset  where the pointer wants it
 */
export function clampOffset(box: GraphBox, offset: Offset): Offset {
  return { dx: Math.max(-box.x, offset.dx), dy: Math.max(-box.y, offset.dy) }
}

/**
 * One anchor moved by its box's offset — this is edges following their node.
 *
 * @param anchor  where the row sits in the placement
 * @param offset  how far its box has been dragged
 * @flow  an untouched box hands back the very same anchor, so the curve string
 *        it produces is identical to the one before any dragging began
 */
export function shiftAnchor(anchor: EdgeAnchor, offset: Offset): EdgeAnchor {
  if (offset.dx === 0 && offset.dy === 0) return anchor
  return {
    left: anchor.left + offset.dx,
    right: anchor.right + offset.dx,
    y: anchor.y + offset.dy
  }
}

/**
 * The offsets keyed by box index, which is all an anchor knows of its box.
 *
 * Only the moved paths are walked: at 105 boxes and one being dragged, this is
 * a map of one, rebuilt per frame and costing nothing.
 *
 * @param layout   the placement, for path -> box index
 * @param offsets  every box that has been moved
 * @flow  each moved path finds its box ; one naming no box is skipped
 */
export function offsetsByBox(layout: GraphLayout, offsets: Offsets): Map<number, Offset> {
  const out = new Map<number, Offset>()
  for (const [path, offset] of offsets) {
    const at = layout.boxOf.get(path)
    if (at !== undefined) out.set(at, offset)
  }
  return out
}

/**
 * How big the canvas has to be to still hold every box that was dragged.
 *
 * @param layout   the placement
 * @param offsets  every box that has been moved
 * @flow  nothing moved -> the placement's own size ; otherwise each moved box
 *        may stretch it right and down, and none of them may shrink it
 */
export function canvasSize(
  layout: GraphLayout,
  offsets: Offsets
): { width: number; height: number } {
  let width = layout.width
  let height = layout.height
  for (const [path, offset] of offsets) {
    const at = layout.boxOf.get(path)
    if (at === undefined) continue
    const box = layout.boxes[at]
    width = Math.max(width, box.x + offset.dx + box.width)
    height = Math.max(height, box.y + offset.dy + box.height)
  }
  return { width, height }
}

// ---------------------------------------------------------------- viewport
//
// Where the reader is looking, in the canvas's own units rather than in client
// pixels. `scrollLeft` is pixels, so it means something different at every zoom
// and something different again after a level change moves every box — which is
// exactly why zooming out of a large graph normally loses your place. Everything
// below converts in one direction or the other so that it does not.

/** A rectangle of the canvas, in user units. */
export interface Viewport {
  x: number
  y: number
  w: number
  h: number
}

/**
 * The user-unit point currently under one place in the window.
 *
 * The other direction from `anchorScroll`, and the first half of zooming about
 * the cursor: read what is under the pointer *before* the scale moves, then ask
 * for it back afterwards.
 *
 * @param scroll  the container's scroll position, in pixels
 * @param at      a place in the container, in client pixels from its top-left
 * @param zoom    the surface's zoom factor
 * @flow  a zoom of zero reads as one rather than dividing by it, exactly as
 *        `viewportOf` does
 */
export function pointAt(
  scroll: { left: number; top: number },
  at: { x: number; y: number },
  zoom: number
): { x: number; y: number } {
  const z = zoom > 0 ? zoom : 1
  return { x: (scroll.left + at.x) / z, y: (scroll.top + at.y) / z }
}

/**
 * The scroll that puts one canvas point under one place in the window.
 *
 * @param point  where to look, in user units
 * @param at     where in the container it should land, in client pixels
 * @param zoom   the surface's zoom factor
 * @param view   the scroll container's client size, in pixels
 * @param size   the canvas, in user units
 * @flow  clamped at both ends: neither before the origin, which the viewBox
 *        cuts, nor past what there is to scroll — so a point near an edge lands
 *        as close as the canvas allows rather than nowhere
 */
export function anchorScroll(
  point: { x: number; y: number },
  at: { x: number; y: number },
  zoom: number,
  view: { width: number; height: number },
  size: { width: number; height: number }
): { left: number; top: number } {
  const z = zoom > 0 ? zoom : 1
  return {
    left: Math.min(Math.max(0, point.x * z - at.x), Math.max(0, size.width * z - view.width)),
    top: Math.min(Math.max(0, point.y * z - at.y), Math.max(0, size.height * z - view.height))
  }
}

/**
 * The scroll position that puts one point of the canvas in the middle.
 *
 * The middle is just one place to anchor to, so this is `anchorScroll` at half
 * the view — same arithmetic it always had, said once instead of twice.
 *
 * @param point  where to look, in user units
 * @param zoom   the surface's zoom factor
 * @param view   the scroll container's client size, in pixels
 * @param size   the canvas, in user units
 */
export function centerScroll(
  point: { x: number; y: number },
  zoom: number,
  view: { width: number; height: number },
  size: { width: number; height: number }
): { left: number; top: number } {
  return anchorScroll(point, { x: view.width / 2, y: view.height / 2 }, zoom, view, size)
}

/**
 * How far the view moves while a pan is under way.
 *
 * Client pixels on both sides, 1:1 with the hand — a node drag divides its
 * offset by the zoom because that offset is in user units, and a pan does not,
 * because the scroll it moves already is in pixels.
 *
 * @param base   the scroll position the pan started from
 * @param moved  how far the pointer has travelled since, in client pixels
 */
export function panScroll(
  base: { left: number; top: number },
  moved: { dx: number; dy: number }
): { left: number; top: number } {
  return { left: base.left - moved.dx, top: base.top - moved.dy }
}

/**
 * What is on screen right now, in user units.
 *
 * @param scroll  the container's scroll position and client size, in pixels
 * @param zoom    the surface's zoom factor
 */
export function viewportOf(
  scroll: { left: number; top: number; width: number; height: number },
  zoom: number
): Viewport {
  const z = zoom > 0 ? zoom : 1
  return { x: scroll.left / z, y: scroll.top / z, w: scroll.width / z, h: scroll.height / z }
}

/**
 * The path of the box nearest a point — the one thing that survives a level
 * change, since the coordinates themselves do not.
 *
 * @param layout  the placement
 * @param point   a point in user units
 * @flow  squared distance between centres, smallest wins ; no boxes -> null
 */
export function nearestBoxPath(
  layout: GraphLayout,
  point: { x: number; y: number }
): string | null {
  let best: string | null = null
  let bestGap = Number.POSITIVE_INFINITY
  for (const box of layout.boxes) {
    const dx = box.x + box.width / 2 - point.x
    const dy = box.y + box.height / 2 - point.y
    const gap = dx * dx + dy * dy
    if (gap < bestGap) {
      bestGap = gap
      best = box.path
    }
  }
  return best
}

/**
 * The middle of one box, where it now is.
 *
 * @param box     the box as it was placed
 * @param offset  how far it has been dragged
 */
export function boxCenter(box: GraphBox, offset: Offset): { x: number; y: number } {
  return { x: box.x + offset.dx + box.width / 2, y: box.y + offset.dy + box.height / 2 }
}

/**
 * The scale that fits the whole canvas into the minimap, and the size it takes.
 *
 * @param size  the canvas, in user units
 * @param maxW  the widest the minimap may be, in client pixels
 * @param maxH  the tallest
 * @flow  an empty canvas is a zero-sized map rather than a NaN ; a canvas that
 *        already fits is drawn at its own size instead of being magnified
 */
export function minimapFit(
  size: { width: number; height: number },
  maxW = MINIMAP_W,
  maxH = MINIMAP_H
): { scale: number; width: number; height: number } {
  if (!(size.width > 0) || !(size.height > 0)) return { scale: 0, width: 0, height: 0 }
  const scale = Math.min(1, maxW / size.width, maxH / size.height)
  return { scale, width: size.width * scale, height: size.height * scale }
}

/**
 * Where an edge attaches to one function, at whatever level is being drawn.
 *
 * A row that this level does not draw still has a box, and a curve to the side
 * of the box says one thing less rather than nothing at all. That fallback is
 * what keeps a selection made at one zoom meaningful at another.
 *
 * @param layout  the placement
 * @param byId    every function of the index, by id
 * @param byBox   how far each dragged box has been pulled, by box index
 * @param id      the function being attached to
 * @flow  its own row -> that row ; else its file's box -> the box's side ;
 *        a function this index never had -> null
 */
export function resolveAnchor(
  layout: GraphLayout,
  byId: Map<number, GraphFunction>,
  byBox: Map<number, Offset>,
  id: number
): EdgeAnchor | null {
  const anchor = layout.anchors.get(id)
  if (anchor) return shiftAnchor(anchor, byBox.get(anchor.boxIndex) ?? ZERO)
  const path = byId.get(id)?.path
  if (path === undefined) return null
  const at = layout.boxOf.get(path)
  if (at === undefined) return null
  return shiftAnchor(boxAnchor(layout.boxes[at]), byBox.get(at) ?? ZERO)
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
 * @param edges    every file pair the index counted
 * @param layout   the placed boxes, for both ends of each link
 * @param limit    how many links to draw at most
 * @param offsets  the boxes the human has dragged, whose links follow them
 * @flow  drop pairs whose ends are not on this canvas (a half-written DB names
 *        files that have no box) -> heaviest first -> keep `limit` of them ->
 *        one curve each between the two boxes where they now are, its width
 *        scaled against the heaviest kept
 * 주요 내부 변수: usable(양끝이 캔버스에 있는 쌍), heaviest(굵기의 기준)
 */
export function fileLines(
  edges: GraphFileEdge[],
  layout: GraphLayout,
  limit = MAX_FILE_EDGES,
  offsets: Offsets = NO_OFFSETS
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
    const from = shiftAnchor(boxAnchor(layout.boxes[fromBox]), offsetOf(offsets, edge.from))
    const to = shiftAnchor(boxAnchor(layout.boxes[toBox]), offsetOf(offsets, edge.to))
    return {
      key: `${edge.from}->${edge.to}`,
      d: edgePath(from, to),
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

// -------------------------------------------------------------- edge style
//
// One table, three readers: the drawn edge, the row's 2px mark, and the legend.
// Three copies of one mapping is how a legend comes to disagree with the screen,
// which is the regression this section exists to make impossible. The values are
// theme tokens and never literals, and `layout.test.ts` holds every one of them
// against theme.css — a renamed token fails a test instead of quietly painting
// an uncoloured line.

/** Which way an edge points, seen from the selection. */
export type EdgeKind = 'calls' | 'callers'

/** How one kind of edge is drawn: line, dash and arrowhead as one fact. */
export interface EdgeStyle {
  /** A `var(--color-…)` token — never a literal colour. */
  stroke: string
  /** `stroke-dasharray`, or null for a solid line. */
  dash: string | null
  /** The `<marker>` id this kind's arrowhead is drawn by. */
  marker: string
}

/**
 * calls = accent(파랑) 실선, callers = ok(초록) 파선. 색이 유일한 차이가 되지
 * 않도록 파선을 함께 남긴다 — graph-visual의 결정 그대로.
 */
export const EDGE_STYLES: Readonly<Record<EdgeKind, EdgeStyle>> = {
  calls: { stroke: 'var(--color-accent)', dash: null, marker: 'fn-arrow-calls' },
  callers: { stroke: 'var(--color-ok)', dash: '3 3', marker: 'fn-arrow-callers' }
}

/**
 * How one edge of the selection's neighbourhood is drawn.
 *
 * @param incoming  is this someone calling the selection, rather than the
 *                  selection calling out?
 * @flow  incoming reads the callers row of the table, outgoing the calls row
 */
export function edgeStyle(incoming: boolean): EdgeStyle {
  return incoming ? EDGE_STYLES.callers : EDGE_STYLES.calls
}

/**
 * The colour one row mark is drawn in — the same two the edges use.
 *
 * Lives here rather than beside the rows it paints so the mapping has one home,
 * and so a test can hold it against `EDGE_STYLES` without a browser
 * (`tsconfig.test.json` excludes `ui/`).
 *
 * @param mark  what this row is to whatever the pointer is on
 * @flow  outgoing and the selection itself read accent, incoming reads ok, the
 *        rest are neutral
 */
export function markColour(mark: RowMark): string {
  if (mark === 'selected' || mark === 'calls') return EDGE_STYLES.calls.stroke
  if (mark === 'callers') return EDGE_STYLES.callers.stroke
  if (mark === 'hover') return 'var(--color-fg-dim)'
  return 'var(--color-fg-mute)'
}

// ------------------------------------------------------------------- trace
//
// A chain and the places it stops. The chain itself is the DB's answer
// (`readGraphTrace`); everything here is only where it lands on this canvas, so
// it lives beside the placement rather than in the component — `ui/` is the one
// directory `tsconfig.test.json` excludes, and a decision nobody can test is a
// decision that drifts.
//
// Nothing new is invented for the colours: a `calls` trace is the same blue the
// legend already promises for "what this calls", and a `callers` trace the same
// green. That is why no `RowMark` is added and `FileBox` is not touched at all.

/** 추적이 끊긴 자리. 색만이 유일한 차이가 되지 않도록 파선을 함께 쓴다 —
 *  EDGE_STYLES가 세운 규칙 그대로. */
export const TRACE_BREAK_STYLE: Readonly<EdgeStyle> = {
  stroke: 'var(--color-warn)',
  dash: '2 2',
  marker: ''
}

/** 끊김 스텁이 노드 옆으로 뻗는 길이, user unit. */
export const TRACE_STUB_LEN = 26
/** 스텁 끝의 십자 틱이 위아래로 뻗는 길이 — "여기서 끝"의 선 끝 모양. */
export const TRACE_TICK_H = 4
/** 한 트레이스가 그리는 곡선의 상한. 넘으면 얕은 고리부터 남기고 범례가 센다. */
export const MAX_TRACE_EDGES = 500
/** 가장 먼 고리가 내려가는 농도. 0이 아닌 것이 요점이다 — 마지막 고리도 보인다. */
export const TRACE_FAR = 0.45

/**
 * 사슬이 칠하는 행들 — 방향이 곧 색이다.
 *
 * 새 `RowMark`를 만들지 않는다: calls 방향의 사슬은 전부 "이 함수가 부르는 것"
 * 이고 callers 방향은 전부 "이 함수를 부르는 것"이므로, 범례가 이미 약속한 두
 * 색이 그대로 맞는 말이다. 그래서 `FileBox`도 `markColour`도 손대지 않는다.
 *
 * @param trace  DB가 답한 사슬
 * @flow  루트는 'selected' ; 나머지 노드는 방향에 따라 'calls' 또는 'callers'
 */
export function traceMarks(trace: GraphTrace): Map<number, RowMark> {
  const marks = new Map<number, RowMark>()
  const ring: RowMark = trace.direction === 'callers' ? 'callers' : 'calls'
  for (const node of trace.nodes) marks.set(node.id, node.id === trace.rootId ? 'selected' : ring)
  marks.set(trace.rootId, 'selected')
  return marks
}

/**
 * 깊이가 멀수록 옅게. 사슬의 방향을 색이 아니라 농도로 한 번 더 말한다.
 *
 * @param depth  이 걸음이 닿은 깊이
 * @param max    이 트레이스의 깊이
 * @flow  1이 가장 진하고 최대 깊이가 TRACE_FAR까지 내려간다 ; 깊이 1짜리
 *        트레이스는 나눌 것이 없으므로 언제나 1
 */
export function traceOpacity(depth: number, max: number): number {
  if (!(max > 1)) return 1
  const t = Math.min(1, Math.max(0, (depth - 1) / (max - 1)))
  // Interpolated rather than subtracted from 1, so the last ring lands on
  // TRACE_FAR exactly instead of a float a hair below it.
  return (1 - t) + TRACE_FAR * t
}

/** 사슬이 실제로 따라간 호출 하나, 지금 상자들이 있는 자리에서. */
export interface TraceLine {
  key: string
  d: string
  fromId: number
  toId: number
  /** 바깥쪽 끝의 깊이 — 이 선이 얼마나 옅게 그려지는지의 근거. */
  depth: number
}

/** 한 노드가 따라가지 못한 호출부들을, 하나의 짧은 점선으로. */
export interface TraceStub {
  key: string
  /** 점선 본체. */
  d: string
  /** 그 끝의 십자 틱 — "여기서 끝"을 선의 끝 모양으로 말한다. */
  tick: string
  atId: number
  /** 이 노드가 따라가지 못한 호출부의 총 개수. */
  count: number
  /** 툴팁 한 줄: "3 call sites could not be followed: foo, bar, …" */
  label: string
}

export interface TraceDrawing {
  lines: TraceLine[]
  stubs: TraceStub[]
  /** 그린 곡선 수와 사슬이 가진 곡선 수. 범례가 둘 다 인쇄한다. */
  shown: number
  total: number
}

/** How many names one stub's tooltip spells before it says "…". */
const STUB_NAMES = 3

/**
 * 사슬을 곡선으로, 끊김을 스텁으로 — 지금 상자들이 있는 자리에서.
 *
 * 곡선은 언제나 `edgePath(caller, callee)`다. 역방향 추적이라고 화살표를
 * 뒤집으면 그림이 호출 방향에 대해 거짓말을 한다 — 따라간 순서와 부르는 방향은
 * 다른 사실이고, 화면이 말하는 쪽은 후자다.
 *
 * @param trace   DB가 답한 사슬
 * @param layout  이 레벨의 배치
 * @param byId    인덱스의 모든 함수, id별
 * @param byBox   끌어다 놓은 상자들의 이동량, 상자 index별
 * @param lod     이 줌이 그리는 레벨
 * @param limit   곡선 상한
 * @flow  파일 레벨은 함수 곡선을 그리지 않는다(원경이 읽히는 이유가 그것이다) ->
 *        깊이 얕은 것부터 정렬해 limit까지 -> 양 끝의 앵커를 지금 자리에서 읽어
 *        곡선 -> 끊김은 노드당 하나로 접어 스텁 하나 + 십자 틱
 * 주요 내부 변수: byNode(노드별로 접은 끊김), outward(스텁이 뻗는 방향)
 */
export function traceLines(
  trace: GraphTrace,
  layout: GraphLayout,
  byId: Map<number, GraphFunction>,
  byBox: Map<number, Offset>,
  lod: Lod,
  limit = MAX_TRACE_EDGES
): TraceDrawing {
  const total = trace.edges.length
  // The far view keeps the file links and drops every function curve, exactly as
  // `edgesFor` does. `total` is still reported, so the legend can say the chain
  // is there and merely not drawn at this scale.
  if (lod === 'file') return { lines: [], stubs: [], shown: 0, total }

  const ordered = trace.edges
    .slice()
    .sort((a, b) =>
      a.depth !== b.depth
        ? a.depth - b.depth
        : a.from !== b.from
          ? a.from - b.from
          : a.to - b.to
    )
  const lines: TraceLine[] = []
  for (const edge of ordered) {
    if (lines.length >= Math.max(0, limit)) break
    const from = resolveAnchor(layout, byId, byBox, edge.from)
    const to = resolveAnchor(layout, byId, byBox, edge.to)
    if (!from || !to) continue
    lines.push({
      key: `t${edge.from}->${edge.to}`,
      d: edgePath(from, to),
      fromId: edge.from,
      toId: edge.to,
      depth: edge.depth
    })
  }

  // Folded per node: a function with twelve unfollowable names would otherwise
  // get twelve stubs drawn on top of each other, which says nothing at all.
  const byNode = new Map<number, { count: number; names: string[] }>()
  for (const stop of trace.breaks) {
    const had = byNode.get(stop.atId)
    if (had) {
      had.count += stop.count
      if (!had.names.includes(stop.callee)) had.names.push(stop.callee)
      continue
    }
    byNode.set(stop.atId, { count: stop.count, names: [stop.callee] })
  }

  const outward = trace.direction === 'callers' ? -1 : 1
  const stubs: TraceStub[] = []
  for (const [atId, stop] of byNode) {
    const at = resolveAnchor(layout, byId, byBox, atId)
    if (!at) continue
    const root = trace.direction === 'callers' ? at.left : at.right
    const tip = root + outward * TRACE_STUB_LEN
    const named = stop.names.slice(0, STUB_NAMES).join(', ')
    const rest = stop.names.length > STUB_NAMES ? ', …' : ''
    const sites = `${stop.count} call site${stop.count === 1 ? '' : 's'}`
    stubs.push({
      key: `b${atId}`,
      d: `M ${root} ${at.y} L ${tip} ${at.y}`,
      tick: `M ${tip} ${at.y - TRACE_TICK_H} L ${tip} ${at.y + TRACE_TICK_H}`,
      atId,
      count: stop.count,
      label: `${sites} could not be followed: ${named}${rest}`
    })
  }

  return { lines, stubs, shown: lines.length, total }
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

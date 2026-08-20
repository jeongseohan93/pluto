import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent
} from 'react'
import {
  TRACE_DEPTH_DEFAULT,
  TRACE_DEPTH_MAX,
  TRACE_DEPTH_MIN,
  clampTraceDepth,
  type GraphEdge,
  type GraphFileEdge,
  type GraphFileGroup,
  type GraphFunction,
  type GraphIndexResult,
  type GraphNodeDetail,
  type GraphTrace,
  type TraceDirection
} from '@domains/graph-view/types'
import {
  EDGE_STYLES,
  FILE_BOX_H,
  FILE_LABEL_PX,
  FILE_META_PX,
  HEADER_H,
  KEY_FN_PER_FILE,
  MAX_FILE_EDGES,
  NO_OFFSETS,
  ROW_H,
  TRACE_BREAK_STYLE,
  ZOOM_DETAIL,
  anchorScroll,
  boxCenter,
  buildAdjacency,
  callerCounts,
  canvasSize,
  centerScroll,
  clampOffset,
  edgePath,
  edgeStyle,
  fileLines,
  fitChars,
  flattenFunctions,
  groupMarks,
  isDrag,
  layoutGraph,
  lodFor,
  markColour,
  matchFunctions,
  mergeMarks,
  nearestBoxPath,
  neighbourMarks,
  offsetOf,
  offsetsByBox,
  panScroll,
  pointAt,
  resolveAnchor,
  traceLines,
  traceMarks,
  traceOpacity,
  viewportOf,
  withOffset,
  zoomBy,
  type FileLine,
  type GraphBox,
  type GraphLayout,
  type Lod,
  type Offset,
  type Offsets,
  type RowMark,
  type TraceDrawing
} from '@domains/graph-view/layout'
import type { CodeTarget } from '@domains/code-view/types'
import { CodeViewer } from '@domains/code-view/ui/CodeViewer'
import { FunctionDetail } from '@domains/graph-view/ui/FunctionDetail'
import { Minimap } from '@domains/graph-view/ui/Minimap'
import { Icon } from '@renderer/components/Icon'
import { EmptyState } from '@renderer/components/primitives'

/** A stable empty map, so a box with nothing marked keeps identical props. */
const NO_MARKS: Map<number, RowMark> = new Map()
const NO_FILE_EDGES: GraphFileEdge[] = []
/** No index, or an index that could not be read. Shared, so `[files]` is a
 *  dependency that means "a different graph" and not "another render". */
const NO_FILES: GraphFileGroup[] = []
const NO_EDGES: GraphEdge[] = []
/** How much of the whole view survives behind a selection: context, not lines. */
const AGGREGATE_DIM = 0.18
/** How far an unrelated box recedes while something else is in focus. */
const BOX_DIM = 0.3

/** What the pointer is on. */
interface Hover {
  /** The row under the pointer, when it is on one. */
  fn: number | null
  /** The file under the pointer. Held apart from `fn` so that sliding down the
   *  rows of one box never redraws the file links. */
  path: string | null
}

const NO_HOVER: Hover = { fn: null, path: null }

/** A drag in progress: the box, the pointer that owns it, and where it began. */
interface Dragging {
  path: string
  box: GraphBox
  pointerId: number
  /** Where the pointer went down, in client pixels — the slop is measured here. */
  startX: number
  startY: number
  /** Where the box already was when this drag started. */
  base: Offset
}

/** A pan in progress: the pointer that owns it, and where the view was. */
interface Panning {
  pointerId: number
  /** Where the pointer went down, in client pixels — the slop is measured here. */
  startX: number
  startY: number
  /** The scroll position this pan started from. */
  left: number
  top: number
}

/**
 * Is this something the space bar already belongs to?
 *
 * The search box and every toolbar button want a space of their own, and taking
 * it from them to arm a pan would be a worse trade than the pan is worth.
 *
 * @param target  whatever had the keyboard when the key went down
 */
function takesSpace(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'BUTTON'
}

/**
 * B — the Function DB on screen: files as boxes, functions as rows.
 *
 * **Two levels of detail, and that is the whole idea.** This repository's graph
 * holds 8669 call edges. Drawing them at once is not a performance problem so
 * much as a legibility one — it is a hairball, and a hairball answers no
 * question. So the whole view draws one curve per *file pair*, its thickness
 * the number of calls: a few hundred lines that say which files talk to which.
 * Individual function edges appear only around a selection and vanish again
 * when it is cleared. That single decision is also what lets the layout be
 * arithmetic (files in path order, packed into columns) instead of a physics
 * simulation, which is why this slice adds no rendering dependency at all.
 *
 * **The zoom is a scale bar, not a magnifier.** Far out, only file boxes are
 * placed — name, function count, spec coverage — and the canvas shrinks with
 * them, from 6808 units wide to 2044. In between, each file keeps its
 * most-called rows. Close in, everything is drawn, exactly as before. The level
 * is an argument to `layoutGraph` rather than a condition inside a component,
 * so moving the zoom inside one band recomputes nothing at all.
 *
 * **Selection draws, hover only lights up.** A selection is what makes edges
 * appear and dims everything unrelated; hovering merely brightens a row and its
 * direct neighbours. So a pointer crossing 1435 rows never makes a single path
 * appear or disappear, which is the difference between smooth and not.
 *
 * **Grey means no spec.** `has_spec = 0` renders muted, so coverage is
 * something you see rather than something you compute.
 *
 * **A box can be dragged, and only for as long as this layout lives.** The
 * placement stays exactly as `layoutGraph` computed it; a dragged box is a
 * translate on the wrapper `<g>` and a shifted anchor on the curves that touch
 * it, so the 1435 rows inside are never re-rendered by a drag. The positions
 * are deliberately not stored anywhere: a rebuild or a reload is the way back
 * to the arrangement everybody else sees.
 *
 * **Trace is one layer over the selection.** [Trace] follows the picked
 * function's call chain N rings out — `calls` for what it reaches, `callers` for
 * who reaches it — and the chain comes from the DB rather than from
 * `index.edges`, because that list has already dropped every unresolved call and
 * so could never say where the chain *stops*. Those stops are drawn as short
 * dashed stubs beside the node they hang on. Nothing about the picture is
 * replaced while it is on: the chain only changes where `focusMarks` comes from,
 * so the dimming that was already there follows it, and turning the mode off is
 * the whole of going back — there is no restore path to keep correct.
 *
 * **Panning is scrolling, and zooming is about the cursor.** Dragging bare
 * canvas moves the scroll container itself rather than a second transform layer,
 * so the minimap, the scrollbars and `centerScroll` all keep their one shared
 * idea of where the reader is. A drag that started on a box is that box's, as
 * before; holding space makes the whole `<svg>` pointer-transparent, which is
 * both the forced pan and the cursor in one property. The wheel changes the
 * scale instead of the scroll, keeping the point under the pointer where it is —
 * except across a level change, where the placement itself moves and the box
 * under the pointer is centred instead.
 *
 * @param index      the loaded index, or null while it loads
 * @param loading    is a read in flight?
 * @param selected   the focused function id, or null for the whole view
 * @param onSelect   focus another function, or null to return to the whole view
 * @param onBuild    run `aidev graph build`
 * @param busy       a command is already running (one slot)
 * @param zoom       the surface's zoom factor
 * @param onZoomChange  change the scale — how a jump to one function brings the
 *                      level of detail it needs along with it
 * @flow  no index -> a reading state ; index not ok -> the reason and a way out
 *        -> otherwise the canvas, plus the code viewer when a row was
 *        double-clicked, plus the detail panel for the selection, which folds
 *        away and comes back by itself when a row is picked
 * 주요 내부 변수: hover(포인터 아래의 행/파일), focus(선택 여부 = 뷰 모드),
 * offsets(끌어다 놓은 상자들 — 세션 한정), drag(진행 중인 드래그),
 * pan(진행 중인 팬), spaceHeld(스페이스 홀드 = 강제 팬), lod(이 줌이 그리는 레벨),
 * codeTarget/codeOpen(코드 뷰어의 좌표와 표시 여부),
 * traceOn/traceDir/traceDepth(추적 설정), trace(DB가 답한 사슬 — null이면 이
 * 모드가 없던 때와 같은 화면)
 */
export function FunctionGraphSurface({
  index,
  loading,
  selected,
  onSelect,
  onBuild,
  busy,
  zoom,
  onZoomChange
}: {
  index: GraphIndexResult | null
  loading: boolean
  selected: number | null
  onSelect: (id: number | null) => void
  onBuild: () => void
  busy: boolean
  zoom: number
  onZoomChange?: (zoom: number) => void
}): JSX.Element {
  const [detail, setDetail] = useState<GraphNodeDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  /** Where the code viewer is aimed, and whether it is on screen. Two states,
   *  because a caller row moves the aim without summoning a panel nobody asked
   *  for, and a double-click does both. */
  const [codeTarget, setCodeTarget] = useState<CodeTarget | null>(null)
  const [codeOpen, setCodeOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [hover, setHover] = useState<Hover>(NO_HOVER)
  const [offsets, setOffsets] = useState<Offsets>(NO_OFFSETS)
  const [detailOpen, setDetailOpen] = useState(true)
  const [mapOpen, setMapOpen] = useState(true)
  // These two are state and not refs because the cursor and the svg's
  // pointer-events are drawn from them. Each flips once per pan, never per
  // frame: the pan itself moves `scrollLeft` and re-renders nothing at all.
  const [panning, setPanning] = useState(false)
  const [spaceHeld, setSpaceHeld] = useState(false)
  // Trace is one layer *over* the selection, never a replacement for it: with
  // `trace` null the canvas is byte-for-byte what it was before this mode
  // existed, which is what makes "해제 시 원상복귀" a fact about the code rather
  // than a restore path somebody has to keep correct.
  const [traceOn, setTraceOn] = useState(false)
  const [traceDir, setTraceDir] = useState<TraceDirection>('calls')
  const [traceDepth, setTraceDepth] = useState(TRACE_DEPTH_DEFAULT)
  const [trace, setTrace] = useState<GraphTrace | null>(null)
  const canvas = useRef<HTMLDivElement>(null)

  // The drag itself is a ref, not state: a pointer move already re-renders
  // through `offsets`, and there is nothing to gain from a second one.
  const drag = useRef<Dragging | null>(null)
  /** The pan in progress, for the same reason — and so a hover can tell. */
  const pan = useRef<Panning | null>(null)
  /** Did the pointer sequence just ending actually move? A click reads this. */
  const dragged = useRef(false)
  const offsetsRef = useRef(offsets)
  offsetsRef.current = offsets

  /** The last selection this surface already answered by moving the scale. */
  const jumped = useRef<number | null>(null)
  /** 이 요청보다 늦게 도착한 응답은 남의 것이다 — 깊이 버튼을 연달아 누를 때
   *  순서가 뒤바뀐 답이 화면에 남지 않도록. */
  const traceToken = useRef(0)
  /** Read by the Escape handler, which must not re-subscribe per toggle. */
  const traceOnRef = useRef(traceOn)
  traceOnRef.current = traceOn
  /** The zoom and placement the current scroll position was measured against. */
  const view = useRef<{ zoom: number; layout: GraphLayout } | null>(null)
  /** A file to land on at the next placement — the far view's way in. */
  const pendingPath = useRef<string | null>(null)
  /** A point to keep still at the next placement, and where to keep it — the
   *  wheel's anchor, and `pendingPath`'s sibling for a zoom inside one level. */
  const pendingHold = useRef<{ point: { x: number; y: number }; at: { x: number; y: number } } | null>(
    null
  )

  const files = index?.ok ? index.files : NO_FILES
  // A string, so every zoom inside one band is the same dependency and the
  // placement below is not rebuilt for a change that would not alter it.
  const lod = useMemo(() => lodFor(zoom), [zoom])
  const counts = useMemo(() => callerCounts(index?.ok ? index.edges : NO_EDGES), [index])
  const layout = useMemo(() => layoutGraph(files, lod, counts), [files, lod, counts])
  const functions = useMemo(() => flattenFunctions(files), [files])
  // Search reads the whole index, never the drawn rows: a function you cannot
  // see at this zoom is still a function you can look for.
  const results = useMemo(() => matchFunctions(functions, query), [functions, query])
  const byId = useMemo(() => new Map(functions.map((fn) => [fn.id, fn])), [functions])

  // Everything the drag moves, and nothing it does not: `layout` above does not
  // depend on `offsets`, so the marks and the boxes are left alone by a drag.
  const byBox = useMemo(() => offsetsByBox(layout, offsets), [layout, offsets])
  const size = useMemo(() => canvasSize(layout, offsets), [layout, offsets])

  const fileEdges = index?.ok ? index.fileEdges : NO_FILE_EDGES
  const links = useMemo(
    () => fileLines(fileEdges, layout, MAX_FILE_EDGES, offsets),
    [fileEdges, layout, offsets]
  )
  // Folded once from the index, not queried per hover: at 1435 rows, one round
  // trip per row the pointer crosses is exactly the lag being avoided here.
  const adjacency = useMemo(() => buildAdjacency(index?.ok ? index.edges : NO_EDGES), [index])

  // Read inside effects that must not *re-run* when these change. `zoom` and
  // `size` move with the level; the scroll should follow the selection, not
  // chase every rung of the ladder.
  const zoomRef = useRef(zoom)
  zoomRef.current = zoom
  const sizeRef = useRef(size)
  sizeRef.current = size
  const layoutRef = useRef(layout)
  layoutRef.current = layout
  const byIdRef = useRef(byId)
  byIdRef.current = byId
  const byBoxRef = useRef(byBox)
  byBoxRef.current = byBox

  // The node's own record, which is also where its edges come from.
  useEffect(() => {
    if (selected === null) {
      setDetail(null)
      return
    }
    let alive = true
    setDetailLoading(true)
    window.aidev.getGraphNode(selected).then((next) => {
      if (!alive) return
      setDetail(next)
      setDetailLoading(false)
    })
    return () => {
      alive = false
    }
  }, [selected])

  // The chain, asked of the DB rather than walked in memory: `index.edges` has
  // already dropped every unresolved call, so a chain computed here could not
  // say where it stops. `index` is a dependency because a rebuild reissues every
  // `functions.id` (`db.py` replace_file drops and re-inserts) — an old chain's
  // ids would light up whichever functions inherited them.
  useEffect(() => {
    if (!traceOn || selected === null) {
      setTrace(null)
      return
    }
    const token = ++traceToken.current
    // The previous chain is left on screen while this one loads: stepping the
    // depth up a ring should not blink the whole canvas back to nothing.
    window.aidev
      .getGraphTrace({ id: selected, direction: traceDir, depth: clampTraceDepth(traceDepth) })
      .then((next) => {
        if (token !== traceToken.current) return
        setTrace(next)
      })
  }, [traceOn, selected, traceDir, traceDepth, index])

  // A selection nobody can see is a selection nobody made: the panel comes back
  // by itself rather than leaving a picked row with nothing said about it.
  useEffect(() => {
    if (selected !== null) setDetailOpen(true)
  }, [selected])

  // Dragged positions are of *this graph* and no other. A rebuild or a reload
  // makes new files, and the boxes are back where everybody else sees them —
  // which is how "not saved anywhere" is kept true without any code to save.
  // Keyed on the files rather than the layout, or a single zoom step would
  // throw away an arrangement the reader built by hand.
  useEffect(() => {
    setOffsets((prev) => (prev.size === 0 ? prev : NO_OFFSETS))
  }, [files])

  // A selection that this level does not draw is a selection nobody can see, so
  // picking one brings the scale along with it. Only a *new* selection does
  // this: otherwise deliberately zooming out with a row picked would spring
  // straight back in.
  useEffect(() => {
    if (selected === null) {
      jumped.current = null
      return
    }
    if (jumped.current === selected) return
    jumped.current = selected
    if (!layoutRef.current.anchors.has(selected)) onZoomChange?.(ZOOM_DETAIL)
  }, [selected, onZoomChange])

  // Keep looking at what you were looking at. `scrollLeft` is client pixels, so
  // it points somewhere else entirely once the zoom moves, and a level change
  // moves every box besides. The one thing that survives both is a file's path,
  // so the box nearest the middle of the old view is put back in the middle of
  // the new one. Before paint, or the canvas visibly jumps first and corrects.
  useLayoutEffect(() => {
    const node = canvas.current
    const was = view.current
    view.current = { zoom, layout }
    if (!node || !was || (was.zoom === zoom && was.layout === layout)) return

    // The wheel already knows what it wants kept still and where, so it says so
    // rather than being re-derived from a middle it was never aiming at.
    const hold = pendingHold.current
    pendingHold.current = null
    if (hold) {
      const to = anchorScroll(
        hold.point,
        hold.at,
        zoom,
        { width: node.clientWidth, height: node.clientHeight },
        sizeRef.current
      )
      node.scrollTo({ left: to.left, top: to.top })
      return
    }

    const want = pendingPath.current
    pendingPath.current = null
    const port = viewportOf(
      {
        left: node.scrollLeft,
        top: node.scrollTop,
        width: node.clientWidth,
        height: node.clientHeight
      },
      was.zoom
    )
    const path =
      want ?? nearestBoxPath(was.layout, { x: port.x + port.w / 2, y: port.y + port.h / 2 })
    const at = path === null ? undefined : layout.boxOf.get(path)
    if (at === undefined) return
    const to = centerScroll(
      boxCenter(layout.boxes[at], offsetOf(offsetsRef.current, layout.boxes[at].path)),
      zoom,
      { width: node.clientWidth, height: node.clientHeight },
      sizeRef.current
    )
    node.scrollTo({ left: to.left, top: to.top })
  }, [zoom, layout])

  // Bring the selection into view wherever it came from — a click, a search
  // hit, a caller in the panel, or the file list on the left. A row this level
  // does not draw lands on its file's box instead of silently doing nothing,
  // which is what used to make a search hit at the wrong zoom go nowhere. Being
  // a plain effect, this runs after the one above and so wins the scroll.
  useEffect(() => {
    const node = canvas.current
    if (!node || selected === null) return
    const at = resolveAnchor(layout, byIdRef.current, byBoxRef.current, selected)
    if (!at) return
    const to = centerScroll(
      { x: (at.left + at.right) / 2, y: at.y },
      zoomRef.current,
      { width: node.clientWidth, height: node.clientHeight },
      sizeRef.current
    )
    node.scrollTo({ left: to.left, top: to.top, behavior: 'smooth' })
  }, [selected, layout])

  // Escape is the third way back to the whole view, and since v0.2.8 it peels
  // one layer at a time: the trace first, then the selection. The search box
  // keeps its own Escape — clearing what you typed comes before either.
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return
      const target = event.target as HTMLElement | null
      if (target && target.tagName === 'INPUT') return
      if (traceOnRef.current) {
        setTraceOn(false)
        return
      }
      onSelect(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onSelect])

  // Space held is "pan even over a box", the convention every canvas editor
  // shares. Kept apart from the Escape listener above because it is a different
  // question with a different guard list, and folding them would mean one
  // handler that has to ask which key it is twice.
  useEffect(() => {
    const down = (event: KeyboardEvent): void => {
      // `code`, not `key`: the space bar is the space bar on every layout.
      if (event.code !== 'Space' || event.repeat) return
      // Arming mid-drag would turn the svg pointer-transparent underneath a live
      // capture; releasing the box first is cheaper than relying on what each
      // engine does about that.
      if (takesSpace(event.target) || drag.current) return
      // Or the scroll container pages down for as long as the hold lasts.
      event.preventDefault()
      setSpaceHeld(true)
    }
    const up = (event: KeyboardEvent): void => {
      if (event.code === 'Space') setSpaceHeld(false)
    }
    // Alt-tabbing away never sends the keyup, and a pan left armed forever is a
    // canvas whose boxes have stopped moving for no reason anyone can see.
    const off = (): void => setSpaceHeld(false)
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', off)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', off)
    }
  }, [])

  // The wheel is the scale, centred on the pointer — with a pan on bare canvas
  // there is another way to move, and there was no other way to zoom without
  // reaching for the toolbar. A *native* listener with `passive: false`: React
  // registers wheel passively at the root, so `preventDefault` inside an
  // `onWheel` prop is a silent no-op and the canvas would scroll as well as
  // zoom. Everything it reads is a ref that render keeps current, so it is
  // subscribed once instead of re-bound on every notch.
  const graphed = index?.ok === true
  useEffect(() => {
    const node = canvas.current
    // No way to change the scale -> leave the wheel to its ordinary scrolling.
    if (!node || !graphed || !onZoomChange) return
    const onWheel = (event: WheelEvent): void => {
      // Also at the ends of the range: otherwise the canvas scrolls at exactly
      // the moment the zoom has stopped answering.
      event.preventDefault()
      const from = zoomRef.current
      const next = zoomBy(from, event.deltaY, event.deltaMode)
      if (next === from) return
      const rect = node.getBoundingClientRect()
      const at = { x: event.clientX - rect.left, y: event.clientY - rect.top }
      const point = pointAt({ left: node.scrollLeft, top: node.scrollTop }, at, from)
      // Keeping a point still is only meaningful while the placement stays put.
      // Across a level change every box moves, so the box under the pointer
      // becomes the box in the middle instead — the same hand-over `pickFile`
      // uses on its way in from the far view.
      if (lodFor(next) === lodFor(from)) pendingHold.current = { point, at }
      else pendingPath.current = nearestBoxPath(layoutRef.current, point)
      onZoomChange(next)
    }
    node.addEventListener('wheel', onWheel, { passive: false })
    return () => node.removeEventListener('wheel', onWheel)
  }, [graphed, onZoomChange])

  const traced: TraceDrawing | null = useMemo(
    () => (trace ? traceLines(trace, layout, byId, byBox, lod) : null),
    [trace, layout, byId, byBox, lod]
  )

  // While a trace is up, the selection's own edges are not also drawn: the first
  // ring already is them, and laying the opposite direction over the chain would
  // blur what "추적" is showing.
  const edges = useMemo<DrawnEdge[]>(
    () => (trace ? [] : edgesFor(detail, layout, byId, byBox, lod)),
    [trace, detail, layout, byId, byBox, lod]
  )

  // What the selection marks: itself, what it calls, what calls it. Read off
  // the drawn edges rather than the adjacency, so an unresolved call site is
  // marked in exactly the place it is drawn. A trace only changes where this
  // comes from — the dimming below reads it unchanged and so follows the chain
  // by itself, which is the whole of "기존 디밍 인프라 재사용".
  const focusMarks = useMemo(() => {
    if (trace) return traceMarks(trace)
    if (selected === null) return NO_MARKS
    const outs = new Map<number, RowMark>()
    const ins = new Map<number, RowMark>()
    for (const edge of edges) {
      if (edge.incoming) ins.set(edge.fromId, 'callers')
      else outs.set(edge.toId, 'calls')
    }
    const self = new Map<number, RowMark>([[selected, 'selected']])
    return mergeMarks(mergeMarks(outs, ins), self)
  }, [trace, selected, edges])

  const hoverMarks = useMemo(
    () => (hover.fn === null ? NO_MARKS : neighbourMarks(adjacency, hover.fn)),
    [hover.fn, adjacency]
  )

  // Which rows each box has to draw, and how. Boxes with nothing to show are
  // not keys here at all, so they are handed the same empty map every time and
  // `memo` skips them on every selection and every hover.
  const marks = useMemo(
    () => groupMarks(layout.anchors, mergeMarks(focusMarks, hoverMarks)),
    [layout, focusMarks, hoverMarks]
  )

  // Dimming follows the *selection* only. Were it to follow the hover as well,
  // every pointer move would rewrite 105 opacity attributes for no new fact.
  const focusBoxes = useMemo(() => {
    const set = new Set<number>()
    for (const id of focusMarks.keys()) {
      const anchor = layout.anchors.get(id)
      if (anchor) {
        set.add(anchor.boxIndex)
        continue
      }
      // No row for it at this level: its file stays lit anyway, so a selection
      // made close in is still visible as a box after zooming out.
      const path = byId.get(id)?.path
      const at = path === undefined ? undefined : layout.boxOf.get(path)
      if (at !== undefined) set.add(at)
    }
    return set
  }, [focusMarks, layout, byId])

  // Which boxes the hovered file links to. Read from the full edge list rather
  // than the drawn one: a link the cap left out is still a link.
  const hotBoxes = useMemo(() => {
    const set = new Set<number>()
    if (hover.path === null) return set
    const self = layout.boxOf.get(hover.path)
    if (self !== undefined) set.add(self)
    for (const edge of fileEdges) {
      const other = edge.from === hover.path ? edge.to : edge.to === hover.path ? edge.from : null
      if (other === null) continue
      const at = layout.boxOf.get(other)
      if (at !== undefined) set.add(at)
    }
    return set
  }, [hover.path, layout, fileEdges])

  const focus = selected !== null
  const selectedBox = selected === null ? -1 : (layout.anchors.get(selected)?.boxIndex ?? -1)

  // The far view's labels are sized in client pixels rather than user units, or
  // an 11px name would be 5.5px at 50% and the file boxes would be unreadable
  // rectangles. Only the file level reads this, so at every other level
  // `FileBox`'s props stay independent of the zoom and its memo holds.
  const labelScale = lod === 'file' ? 1 / Math.max(zoom, 0.1) : 1

  // A ref rather than a dependency: the row callback has to stay referentially
  // identical across selections, or every box re-renders and `memo` buys
  // nothing at all.
  const selectedRef = useRef(selected)
  selectedRef.current = selected

  const pick = useCallback(
    (id: number) => {
      setQuery('')
      onSelect(id)
    },
    [onSelect]
  )

  /** Clicking the selected row again is the second way back to the whole view. */
  const pickRow = useCallback(
    (id: number) => {
      setQuery('')
      onSelect(selectedRef.current === id ? null : id)
    },
    [onSelect]
  )

  /**
   * Move the code viewer's aim, and nothing else.
   *
   * A caller row is a coordinate, not a request for a panel: if the viewer is
   * open it travels there, and if it is not, nothing pops up. `onOpenCode` and
   * the double-click are the two things that actually summon it.
   *
   * @param next  where to look
   */
  const reveal = useCallback((next: CodeTarget): void => {
    setCodeTarget(next)
  }, [])

  /**
   * Summon the viewer at a coordinate — the node panel's [코드 보기].
   *
   * @param next  where to look
   */
  const openCode = useCallback((next: CodeTarget): void => {
    setCodeTarget(next)
    setCodeOpen(true)
  }, [])

  /**
   * A row was double-clicked: select it *and* open its source.
   *
   * `useCallback` is not tidiness here. `FileBox` is memoised because 105 boxes
   * and 1435 rows are reconciled otherwise, and a new function identity on
   * every render would undo that on its own.
   *
   * @param fn  the function whose row was double-clicked
   */
  const openRow = useCallback(
    (fn: GraphFunction): void => {
      // A double-click is also a selection — the edges and the marks should
      // land on the row the reader just went to.
      onSelect(fn.id)
      setCodeTarget({
        path: fn.path,
        line: fn.lineno,
        endLine: fn.endLineno || fn.lineno,
        label: fn.qualname
      })
      setCodeOpen(true)
    },
    [onSelect]
  )

  /**
   * The far view's way in: a file box is clicked, and the level that shows its
   * functions arrives with that file in the middle of it.
   *
   * @param path  the file whose box was clicked
   */
  const pickFile = useCallback(
    (path: string): void => {
      // Without a way to change the scale there is nowhere to go, and a
      // remembered path would then hijack whatever moved the level next.
      if (dragged.current || !onZoomChange) return
      pendingPath.current = path
      onZoomChange(ZOOM_DETAIL)
    },
    [onZoomChange]
  )

  /** Fold the minimap away, or bring it back. Takes no arguments. */
  const toggleMap = useCallback((): void => {
    setMapOpen((prev) => !prev)
  }, [])

  /** Follow the chain out of the selection, or stop following it. Takes no
   *  arguments — turning it off is the whole of "해제", and there is nothing to
   *  restore because nothing was replaced. */
  const toggleTrace = useCallback((): void => {
    setTraceOn((prev) => !prev)
  }, [])

  /**
   * How far to follow. The buttons already stop at the ends; the clamp is here
   * as well so that one function decides the range and not three.
   *
   * @param next  the depth the −/+ pair is asking for
   */
  const changeDepth = useCallback((next: number): void => {
    setTraceDepth(clampTraceDepth(next))
  }, [])

  /** Everything the trace toolbar needs, in one object so `FreshnessBar` keeps
   *  its one-prop-per-thing shape instead of growing six. */
  const traceUi = useMemo<TraceUi>(
    () => ({
      on: traceOn,
      direction: traceDir,
      depth: traceDepth,
      ready: selected !== null,
      onToggle: toggleTrace,
      onDirection: setTraceDir,
      onDepth: changeDepth
    }),
    [traceOn, traceDir, traceDepth, selected, toggleTrace, changeDepth]
  )

  /** A new pointer sequence has begun: whatever the last one was, it is over. */
  const clearDragFlag = useCallback((): void => {
    dragged.current = false
  }, [])

  /**
   * Take hold of a box. The capture is what keeps the rest of the drag — and
   * the click that ends it — inside this one `<g>`, wherever the pointer goes.
   *
   * @param event  the pointerdown on the box's wrapper
   * @param box    the box under it
   * @flow  anything but the primary button is left alone
   */
  const startDrag = useCallback((event: ReactPointerEvent<SVGGElement>, box: GraphBox): void => {
    if (event.button !== 0) return
    // Otherwise the browser starts its own image drag and the text selection
    // that `user-select: none` is only half of.
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = {
      path: box.path,
      box,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      base: offsetOf(offsetsRef.current, box.path)
    }
  }, [])

  /**
   * Move the held box, once the pointer has said it means it.
   *
   * @param event  a pointermove, captured back to the box that was grabbed
   * @flow  no drag or another pointer -> nothing ; still inside the slop ->
   *        this is a click and the box stays where it is
   */
  const moveDrag = useCallback(
    (event: ReactPointerEvent<SVGGElement>): void => {
      const at = drag.current
      if (!at || at.pointerId !== event.pointerId) return
      const mx = event.clientX - at.startX
      const my = event.clientY - at.startY
      if (!dragged.current && !isDrag(mx, my)) return
      dragged.current = true
      // One user unit is `zoom` client pixels, so the box keeps up with the
      // cursor at 60% as exactly as it does at 180%.
      const z = zoom || 1
      const to = clampOffset(at.box, { dx: at.base.dx + mx / z, dy: at.base.dy + my / z })
      setOffsets((prev) => withOffset(prev, at.path, to))
    },
    [zoom]
  )

  /**
   * Let go. `dragged` stays set — the click right behind this one reads it.
   *
   * @param event  the pointerup, or the cancel that stands in for one
   */
  const endDrag = useCallback((event: ReactPointerEvent<SVGGElement>): void => {
    const at = drag.current
    if (!at || at.pointerId !== event.pointerId) return
    drag.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }, [])

  /**
   * Take hold of the canvas itself. The hit test is one question — did this
   * pointerdown come from inside a box? — asked of the DOM rather than of
   * coordinates, so there is no second opinion about where the boxes are.
   *
   * Nothing is claimed here: no capture is taken until the pan is known to be
   * one. A pointer that turns out to be a click has therefore been touched by
   * none of this, and reaches the row or the background rect exactly as it did
   * before there was a pan at all.
   *
   * @param event  the pointerdown on the scroll container
   * @flow  anything but the primary button, a drag already under way, or a
   *        pointer that started on a box is left alone ; while space is held the
   *        svg is pointer-transparent, so nothing can ever be under it
   */
  const startPan = useCallback((event: ReactPointerEvent<HTMLDivElement>): void => {
    if (event.button !== 0 || pan.current || drag.current) return
    if (event.target instanceof Element && event.target.closest('[data-box]')) return
    // Otherwise the browser starts its own text selection across the canvas.
    event.preventDefault()
    // The svg's own capture-phase reset never fired if space is held.
    dragged.current = false
    pan.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      left: event.currentTarget.scrollLeft,
      top: event.currentTarget.scrollTop
    }
  }, [])

  /**
   * Move the whole view with the hand.
   *
   * The capture is taken here rather than at the pointerdown, so that it lasts
   * exactly as long as `panning` does. That equivalence is what keeps the svg's
   * `pointer-events: none` from ever outliving the pan that asked for it — the
   * browser guarantees a `lostpointercapture` for every capture it grants,
   * however the pointer ends, and that is one of the three ways out below.
   *
   * @param event  a pointermove, captured back to the container once committed
   * @flow  no pan or another pointer -> nothing ; still inside the slop -> this
   *        is a click on bare canvas and the selection it clears
   */
  const movePan = useCallback((event: ReactPointerEvent<HTMLDivElement>): void => {
    const at = pan.current
    if (!at || at.pointerId !== event.pointerId) return
    const mx = event.clientX - at.startX
    const my = event.clientY - at.startY
    if (!dragged.current && !isDrag(mx, my)) return
    if (!dragged.current) {
      dragged.current = true
      event.currentTarget.setPointerCapture(event.pointerId)
      setPanning(true)
    }
    const to = panScroll(at, { dx: mx, dy: my })
    event.currentTarget.scrollLeft = to.left
    event.currentTarget.scrollTop = to.top
  }, [])

  /**
   * Let go. `dragged` stays set — the click right behind this one reads it.
   *
   * Three events arrive here and the first one wins: the pointerup that ends an
   * ordinary pan, the cancel a touch gesture sends instead, and the lost capture
   * that is the browser's own last word on a pointer — the one that fires even
   * when the window never sees the release.
   *
   * @param event  whichever of the three ended this pointer
   * @flow  no pan or another pointer -> nothing, which is also how the second
   *        and third of the three find that the first has already been here ;
   *        a capture still held is released, and one already lost is not
   */
  const endPan = useCallback((event: ReactPointerEvent<HTMLDivElement>): void => {
    const at = pan.current
    if (!at || at.pointerId !== event.pointerId) return
    pan.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    setPanning(false)
  }, [])

  /**
   * A drag that ends on a row must not also select it — nor, since v0.2.7,
   * open the code viewer on it: a box carried across the canvas and released
   * emits a dblclick of its own.
   *
   * @param event  the click or double-click that follows the pointer's release
   * @flow  the capture phase is early enough to reach the row's own handler
   */
  const swallowClick = useCallback((event: ReactMouseEvent<SVGGElement>): void => {
    if (dragged.current) event.stopPropagation()
  }, [])

  /**
   * Nor must one that ends on bare canvas clear the selection. Takes no
   * arguments — it is the background rect's click, guarded.
   */
  const clearSelection = useCallback((): void => {
    if (dragged.current) return
    onSelect(null)
  }, [onSelect])

  /** Put every box back where the layout placed it. Takes no arguments. */
  const resetOffsets = useCallback((): void => {
    setOffsets(NO_OFFSETS)
  }, [])

  /**
   * Light up the row under the pointer. One listener per box instead of one per
   * row: 1435 row listeners is 1435 closures rebuilt on every render, and the
   * row is in the event anyway.
   *
   * @param target  what the pointer is actually over, row or not
   * @param path    the file whose box this listener belongs to
   * @flow  a drag or a pan in progress -> nothing, or the marks flicker under a
   *        moving canvas ; not on a row -> the file alone is hovered
   */
  const enter = useCallback((target: EventTarget | null, path: string): void => {
    if (drag.current || pan.current) return
    const node = target instanceof Element ? target.closest('[data-fn]') : null
    const raw = node === null ? null : node.getAttribute('data-fn')
    const id = raw === null ? Number.NaN : Number(raw)
    const fn = Number.isFinite(id) ? id : null
    setHover((prev) => (prev.fn === fn && prev.path === path ? prev : { fn, path }))
  }, [])

  const leave = useCallback((): void => {
    setHover((prev) => (prev.fn === null && prev.path === null ? prev : NO_HOVER))
  }, [])

  if (!index) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-tiny text-fg-mute">Reading the Function DB…</span>
      </div>
    )
  }

  if (!index.ok) {
    return (
      <div className="flex h-full flex-col">
        <FreshnessBar
          index={index}
          query={query}
          onQuery={setQuery}
          results={results}
          onPick={pick}
          onBuild={onBuild}
          busy={busy}
          loading={loading}
          moved={offsets.size}
          onReset={resetOffsets}
          trace={traceUi}
        />
        <div className="min-h-0 flex-1">
          <EmptyState title={problemTitle(index)} hint={index.detail ?? ''}>
            <button
              type="button"
              disabled={busy}
              onClick={onBuild}
              className="mt-2 rounded-sm border border-line px-2 py-0.5 text-tiny text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
            >
              Build graph
            </button>
            <p className="mt-1 font-mono text-micro text-fg-mute">{index.dbPath}</p>
          </EmptyState>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-w-0 flex-col">
      <FreshnessBar
        index={index}
        query={query}
        onQuery={setQuery}
        results={results}
        onPick={pick}
        onBuild={onBuild}
        busy={busy}
        loading={loading}
        moved={offsets.size}
        onReset={resetOffsets}
        trace={traceUi}
      />

      <div className="flex min-h-0 flex-1">
        {/* The legend and the minimap sit on a layer that does not scroll, so
            neither of them has to be pinned with a sticky trick. */}
        <div className="relative min-w-0 flex-1">
          <div
            ref={canvas}
            className={`h-full w-full overflow-auto bg-app ${
              panning ? 'cursor-grabbing' : spaceHeld ? 'cursor-grab' : ''
            }`}
            onPointerDown={startPan}
            onPointerMove={movePan}
            onPointerUp={endPan}
            onPointerCancel={endPan}
            // The one end-of-pan the window cannot miss. Without it a capture
            // lost some other way would leave `panning` set, and with it the
            // whole graph pointer-transparent and unselectable for good.
            onLostPointerCapture={endPan}
          >
            <svg
              width={size.width * zoom}
              height={size.height * zoom}
              viewBox={`0 0 ${Math.max(1, size.width)} ${Math.max(1, size.height)}`}
              className="block select-none"
              // The whole forced pan, and the whole cursor, in one property:
              // hit-testing falls through to the container, so `startDrag` can
              // never fire, no box can ever be found under the pointer, and no
              // descendant's own cursor can beat the one below.
              // `pointer-events` is inherited, so this turns off every box and
              // every row at once — which is why both flags have to be certain
              // to clear. `spaceHeld` has the keyup and the window blur;
              // `panning` lasts exactly as long as the container's capture,
              // whose loss `onLostPointerCapture` always hears.
              style={{ pointerEvents: panning || spaceHeld ? 'none' : undefined }}
              onPointerDownCapture={clearDragFlag}
            >
              <defs>
                {/* One arrowhead per edge kind, each painted with that kind's
                    own token. These markers used `context-stroke`, which is a
                    Firefox extension that Chromium does not implement — and
                    marker content inherits from `<defs>`, not from the path
                    that references it, so the arrowheads were resolving to
                    `stroke: none` and never painted at all. The direction the
                    legend promises has to be drawn in a colour this renderer
                    actually has. */}
                <marker
                  id="fn-arrow-calls"
                  viewBox="0 0 8 8"
                  refX="7"
                  refY="4"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path
                    d="M1 1 L7 4 L1 7"
                    fill="none"
                    stroke={EDGE_STYLES.calls.stroke}
                    strokeWidth="1.2"
                  />
                </marker>
                <marker
                  id="fn-arrow-callers"
                  viewBox="0 0 8 8"
                  refX="7"
                  refY="4"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path
                    d="M1 1 L7 4 L1 7"
                    fill="none"
                    stroke={EDGE_STYLES.callers.stroke}
                    strokeWidth="1.2"
                  />
                </marker>
                {/* File links run from 0.5px to 4px wide. In the default marker
                    units the arrowhead scales with the line and the heavy links
                    end in a blot, so this one is measured in user space. */}
                <marker
                  id="fn-arrow-flat"
                  viewBox="0 0 8 8"
                  refX="7"
                  refY="4"
                  markerWidth="8"
                  markerHeight="8"
                  markerUnits="userSpaceOnUse"
                  orient="auto-start-reverse"
                >
                  <path d="M1 1 L7 4 L1 7" fill="none" stroke="context-stroke" strokeWidth="1.2" />
                </marker>
              </defs>

              {/* Clicking bare canvas is the first way back to the whole view,
                  and dragging it is the pan — so it says it can be grabbed.
                  During a pan the svg is transparent and the container's own
                  `grabbing` is what shows through. */}
              <rect
                width={Math.max(1, size.width)}
                height={Math.max(1, size.height)}
                fill="transparent"
                className="cursor-grab"
                onClick={clearSelection}
              />

              {/* LOD, level one: the whole view, one curve per file pair. It
                  survives every zoom — the far view keeps the file links and
                  drops only the function edges. */}
              <FileLinks lines={links.lines} hotPath={hover.path} dim={focus} />

              {/* LOD, level two: function edges exist only around a selection,
                  and only where this zoom draws function rows at all. */}
              <g>
                {edges.map((edge) => {
                  const style = edgeStyle(edge.incoming)
                  return (
                    <path
                      key={edge.key}
                      d={edge.d}
                      fill="none"
                      stroke={style.stroke}
                      strokeWidth={1.4}
                      strokeDasharray={style.dash ?? undefined}
                      strokeOpacity={edge.faint ? 0.55 : 1}
                      markerEnd={`url(#${style.marker})`}
                    />
                  )
                })}
              </g>

              {/* LOD, level two and a half: the traced chain, and the places it
                  stops. Drawn outside the boxes rather than inside a row, so
                  `FileBox`'s memo — 105 boxes and 1435 rows — is untouched by
                  this whole mode. */}
              {traced && trace ? (
                <g pointerEvents="none">
                  {traced.lines.map((line) => {
                    // The style is the direction's, but the arrow is the call's:
                    // caller -> callee, whichever way we walked to find it.
                    const style = edgeStyle(trace.direction === 'callers')
                    return (
                      <path
                        key={line.key}
                        d={line.d}
                        fill="none"
                        stroke={style.stroke}
                        strokeWidth={1.4}
                        strokeDasharray={style.dash ?? undefined}
                        strokeOpacity={traceOpacity(line.depth, trace.depth)}
                        markerEnd={`url(#${style.marker})`}
                      />
                    )
                  })}
                  {/* The stubs take their pointer events back for the tooltip.
                      They carry no `data-box`, so `startPan` still reads them as
                      bare canvas and a drag begun on one still pans. */}
                  {traced.stubs.map((stub) => (
                    <g key={stub.key} pointerEvents="auto">
                      <path
                        d={stub.d}
                        fill="none"
                        stroke={TRACE_BREAK_STYLE.stroke}
                        strokeWidth={1.4}
                        strokeDasharray={TRACE_BREAK_STYLE.dash ?? undefined}
                      />
                      <path
                        d={stub.tick}
                        fill="none"
                        stroke={TRACE_BREAK_STYLE.stroke}
                        strokeWidth={1.4}
                      />
                      <title>{stub.label}</title>
                    </g>
                  ))}
                </g>
              ) : null}

              {/* The drag lives on this wrapper, outside `FileBox`'s memo: only
                  one attribute changes, so the rows inside are not re-rendered
                  even once while the box is being carried across the canvas. */}
              {layout.boxes.map((box, i) => {
                const off = offsets.get(box.path)
                return (
                  <g
                    key={box.path}
                    // What "empty space" means, asked of the DOM: a pointerdown
                    // that finds this above it is a node's, not the canvas's.
                    data-box={box.path}
                    transform={off ? `translate(${off.dx} ${off.dy})` : undefined}
                    opacity={focus && !focusBoxes.has(i) ? BOX_DIM : 1}
                    className="cursor-grab"
                    // Only `opacity` transitions — a dragged box must not lag
                    // 90ms behind the pointer carrying it.
                    style={{ transition: 'opacity 90ms linear', touchAction: 'none' }}
                    onPointerDown={(event) => startDrag(event, box)}
                    onPointerMove={moveDrag}
                    onPointerUp={endDrag}
                    onPointerCancel={endDrag}
                    onClickCapture={swallowClick}
                    onDoubleClickCapture={swallowClick}
                    onMouseOver={(event) => enter(event.target, box.path)}
                    onMouseLeave={leave}
                  >
                    <FileBox
                      box={box}
                      lod={lod}
                      labelScale={labelScale}
                      selectedId={i === selectedBox ? selected : null}
                      marks={marks.get(i) ?? NO_MARKS}
                      hot={hotBoxes.has(i)}
                      onSelect={pickRow}
                      onOpenRow={openRow}
                      onPickFile={lod === 'file' ? pickFile : undefined}
                    />
                  </g>
                )
              })}
            </svg>
          </div>

          <Legend
            shown={links.shown}
            total={links.total}
            lod={lod}
            boxes={layout.boxes.length}
            rows={layout.anchors.size}
            trace={trace && traced ? { trace, drawing: traced } : null}
          />
          <Minimap
            scrollRef={canvas}
            layout={layout}
            offsets={offsets}
            size={size}
            zoom={zoom}
            open={mapOpen}
            onToggle={toggleMap}
          />
        </div>

        {/* The summoned editor, between the canvas and the node panel. There is
            no folded strip for it: [코드 보기] is the way back, and a permanent
            vertical bar would only narrow the canvas for nothing. */}
        {codeOpen ? (
          <div className="flex w-[34rem] min-w-0 shrink-0 flex-col border-l border-line bg-panel">
            <CodeViewer target={codeTarget} onClose={() => setCodeOpen(false)} />
          </div>
        ) : null}

        {detailOpen ? (
          <div className="flex w-80 shrink-0 flex-col border-l border-line bg-panel">
            <div className="flex h-7 shrink-0 items-center gap-1.5 border-b border-line px-2.5">
              <button
                type="button"
                onClick={() => setDetailOpen(false)}
                title="Collapse node detail"
                aria-label="Collapse node detail"
                aria-expanded={true}
                className="shrink-0 text-fg-mute hover:text-fg-dim"
              >
                <Icon name="chevron" size={13} />
              </button>
              <span className="panel-label truncate">Node</span>
            </div>
            <div className="min-h-0 flex-1">
              <FunctionDetail
                detail={detail}
                loading={detailLoading}
                onSelect={pick}
                onReveal={reveal}
                onOpenCode={openCode}
              />
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setDetailOpen(true)}
            title="Show node detail"
            aria-label="Show node detail"
            aria-expanded={false}
            className="flex w-7 shrink-0 items-start justify-center border-l border-line bg-panel pt-2 text-fg-mute hover:text-fg-dim"
          >
            <Icon name="chevron" size={13} className="rotate-180" />
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * B3 — 신선도: which commit this graph is of, when it was taken, and a rebuild.
 *
 * @param index    the index (ok or not — the path is worth showing either way)
 * @param query    the search box's contents
 * @param onQuery  type into the search box
 * @param results  what the query matched
 * @param onPick   focus one of the results
 * @param onBuild  run `aidev graph build`
 * @param busy     a command is already running
 * @param loading  a read is in flight
 * @param moved    how many boxes have been dragged out of place
 * @param onReset  put every dragged box back where the layout placed it
 * @param trace    the trace mode's own controls
 * @flow  meta -> the commit/branch/times line, with dirty and stale called out
 *        ; a box has been dragged -> the way back out of that arrangement
 */
function FreshnessBar({
  index,
  query,
  onQuery,
  results,
  onPick,
  onBuild,
  busy,
  loading,
  moved,
  onReset,
  trace
}: {
  index: GraphIndexResult
  query: string
  onQuery: (value: string) => void
  results: GraphFunction[]
  onPick: (id: number) => void
  onBuild: () => void
  busy: boolean
  loading: boolean
  moved: number
  onReset: () => void
  trace: TraceUi
}): JSX.Element {
  const meta = index.meta
  return (
    <div className="relative flex h-8 shrink-0 items-center gap-2 border-b border-line bg-panel px-2.5">
      <span className="min-w-0 shrink truncate font-mono text-micro text-fg-mute" title={index.dbPath}>
        {meta ? (
          <>
            base <span className="text-fg-dim">{(meta.baseCommit || '?').slice(0, 7)}</span>{' '}
            {meta.branch} · built {stamp(meta.builtAt)} · updated {stamp(meta.updatedAt)}
          </>
        ) : (
          index.dbPath || 'no graph'
        )}
      </span>
      {meta?.dirty ? (
        <span className="shrink-0 font-mono text-micro text-warn" title="the worktree had uncommitted changes when this graph was taken">
          worktree dirty
        </span>
      ) : null}
      {meta?.markedStale ? (
        <span
          className="shrink-0 font-mono text-micro text-warn"
          title=".aidev/graph/dirty exists — code moved since this graph, and no query has paid for the refresh yet"
        >
          stale
        </span>
      ) : null}

      <div className="relative ml-auto flex shrink-0 items-center gap-1.5">
        {moved > 0 ? (
          <button
            type="button"
            onClick={onReset}
            title="put every box back where the layout placed it"
            className="rounded-sm border border-line px-1.5 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-fg"
          >
            Reset positions
          </button>
        ) : null}
        <TraceControls trace={trace} />
        <input
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && results[0]) onPick(results[0].id)
            if (event.key === 'Escape') onQuery('')
          }}
          placeholder="find a function"
          className="w-44 rounded-sm border border-line bg-app px-1.5 py-0.5 font-mono text-micro text-fg placeholder:text-fg-mute"
        />
        <button
          type="button"
          disabled={busy || loading}
          onClick={onBuild}
          title="aidev graph build"
          className="rounded-sm border border-line px-1.5 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
        >
          Rebuild
        </button>
      </div>

      {query.trim() !== '' ? (
        <ul className="absolute top-8 right-2.5 z-20 max-h-72 w-80 overflow-y-auto rounded-sm border border-line bg-panel shadow-lg">
          {results.length === 0 ? (
            <li className="px-2 py-1 text-micro text-fg-mute">no function matches “{query}”</li>
          ) : (
            results.map((fn) => (
              <li key={fn.id}>
                <button
                  type="button"
                  onClick={() => onPick(fn.id)}
                  title={`${fn.qualname} — ${fn.path}:${fn.lineno}`}
                  className="flex w-full items-center gap-2 px-2 py-0.5 text-left font-mono text-micro hover:bg-hover"
                >
                  <span className={`min-w-0 flex-1 truncate ${fn.hasSpec ? 'text-fg-dim' : 'text-fg-mute'}`}>
                    {fn.qualname}
                  </span>
                  <span className="min-w-0 shrink truncate text-fg-mute">
                    {fn.path.split('/').pop()}:{fn.lineno}
                  </span>
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  )
}

/** The trace mode as the toolbar sees it: three settings and three ways to
 *  change them. Held as one object so `FreshnessBar` gains one prop, not six. */
interface TraceUi {
  on: boolean
  direction: TraceDirection
  depth: number
  /** 노드가 선택되어 있는가 — 없으면 따라갈 뿌리가 없으므로 토글은 비활성이다. */
  ready: boolean
  onToggle: () => void
  onDirection: (direction: TraceDirection) => void
  onDepth: (depth: number) => void
}

/**
 * [Trace], and — only once it is on — the direction and the depth.
 *
 * 꺼져 있을 때 버튼 하나만 보이는 것이 설계다: 이 모드를 쓰지 않는 사람의
 * 툴바가 지금보다 붐비지 않아야 한다.
 *
 * @param trace  the mode's settings and the three ways to change them
 * @flow  뿌리가 없으면 토글은 비활성 ; 켜져 있을 때만 방향 세그먼트와 깊이
 *        −/+ 가 나타나고, 각 끝에서 해당 버튼이 비활성이 된다
 */
function TraceControls({ trace }: { trace: TraceUi }): JSX.Element {
  return (
    <div className="flex shrink-0 items-center gap-1 font-mono text-micro">
      <button
        type="button"
        disabled={!trace.ready}
        onClick={trace.onToggle}
        aria-pressed={trace.on}
        title={
          trace.ready
            ? '선택한 함수에서 호출 사슬을 따라간다'
            : '먼저 함수를 하나 고른다'
        }
        className={`rounded-sm border px-1.5 py-0.5 disabled:opacity-40 ${
          trace.on
            ? 'border-accent text-accent'
            : 'border-line text-fg-dim hover:bg-hover hover:text-fg'
        }`}
      >
        Trace
      </button>
      {trace.on ? (
        <>
          {/* 두 칸짜리 세그먼트 — 어느 쪽을 보고 있는지가 언제나 화면에 있다. */}
          <div className="flex overflow-hidden rounded-sm border border-line" role="group">
            <SegButton
              on={trace.direction === 'calls'}
              onClick={() => trace.onDirection('calls')}
              label="calls"
              title="이 함수가 부르는 쪽"
            />
            <SegButton
              on={trace.direction === 'callers'}
              onClick={() => trace.onDirection('callers')}
              label="callers"
              title="누가 여기까지 오나"
            />
          </div>
          <div className="flex items-center gap-0.5">
            <StepButton
              label="−"
              title="한 고리 덜"
              disabled={trace.depth <= TRACE_DEPTH_MIN}
              onClick={() => trace.onDepth(trace.depth - 1)}
            />
            <span
              className="w-6 text-center text-fg-dim"
              title={`추적 깊이 ${TRACE_DEPTH_MIN}..${TRACE_DEPTH_MAX}`}
            >
              d{trace.depth}
            </span>
            <StepButton
              label="+"
              title="한 고리 더"
              disabled={trace.depth >= TRACE_DEPTH_MAX}
              onClick={() => trace.onDepth(trace.depth + 1)}
            />
          </div>
        </>
      ) : null}
    </div>
  )
}

/**
 * One half of the direction segment.
 *
 * @param on       is this the direction being followed?
 * @param onClick  follow this one instead
 * @param label    what it says
 * @param title    what it means, at rest
 */
function SegButton({
  on,
  onClick,
  label,
  title
}: {
  on: boolean
  onClick: () => void
  label: string
  title: string
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={on}
      className={`px-1.5 py-0.5 ${on ? 'bg-hover text-fg' : 'text-fg-mute hover:text-fg-dim'}`}
    >
      {label}
    </button>
  )
}

/**
 * One rung of the depth, either way.
 *
 * @param label     the glyph
 * @param title     what this rung does
 * @param disabled  is the depth already at this end of its range?
 * @param onClick   move it
 */
function StepButton({
  label,
  title,
  disabled,
  onClick
}: {
  label: string
  title: string
  disabled: boolean
  onClick: () => void
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="rounded-sm border border-line px-1 py-0.5 text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-30"
    >
      {label}
    </button>
  )
}

/**
 * The whole view's file→file curves, thickness by call count.
 *
 * Memoised on its own so that pointing at a row inside one box does not
 * reconcile a few hundred paths that did not change: only crossing into
 * another *file* can alter what this draws.
 *
 * @param lines    the placed file links
 * @param hotPath  the file under the pointer, whose links read brighter
 * @param dim      is something selected, making this layer context rather than
 *                 the subject?
 * @flow  one path per link, brightened where it touches the hovered file
 */
const FileLinks = memo(function FileLinks({
  lines,
  hotPath,
  dim
}: {
  lines: FileLine[]
  hotPath: string | null
  dim: boolean
}): JSX.Element {
  return (
    <g
      opacity={dim ? AGGREGATE_DIM : 1}
      style={{ transition: 'opacity 90ms linear' }}
      pointerEvents="none"
    >
      {lines.map((line) => {
        const hot = hotPath !== null && (line.from === hotPath || line.to === hotPath)
        return (
          <path
            key={line.key}
            d={line.d}
            fill="none"
            stroke={hot ? 'var(--color-fg-dim)' : 'var(--color-line-strong)'}
            strokeWidth={line.width}
            markerEnd="url(#fn-arrow-flat)"
          />
        )
      })}
    </g>
  )
})

/**
 * One file box and its function rows.
 *
 * Memoised, and given only what concerns it: a selection somewhere else leaves
 * every prop of this box referentially identical, so it does not re-render.
 * That is what keeps 105 boxes and 1435 rows responsive to a click — and why
 * `groupMarks` omits the boxes with nothing to mark, so they can all be handed
 * the same empty map.
 *
 * @param box         the placed box
 * @param lod         what this zoom draws
 * @param labelScale  1/zoom at the file level, 1 everywhere else — how the far
 *                    view's two lines keep their size on screen
 * @param selectedId  the selection, when it is one of this box's rows
 * @param marks       what each of this box's marked rows is to the pointer
 * @param hot         does the hovered file link to this one?
 * @param onSelect    focus a row, or unfocus it when it is already the one
 * @param onOpenRow   a row was double-clicked: select it and summon its source
 * @param onPickFile  the far view only: go in to this file
 * @flow  the file level -> a two-line card and nothing else ; otherwise the
 *        header, one row per drawn function each drawn by its mark, and a last
 *        line counting whatever this level left out
 * 주요 내부 변수: label/meta(원경 글자 크기, user unit)
 */
const FileBox = memo(function FileBox({
  box,
  lod,
  labelScale,
  selectedId,
  marks,
  hot,
  onSelect,
  onOpenRow,
  onPickFile
}: {
  box: GraphBox
  lod: Lod
  labelScale: number
  selectedId: number | null
  marks: ReadonlyMap<number, RowMark>
  hot: boolean
  onSelect: (id: number) => void
  onOpenRow: (fn: GraphFunction) => void
  onPickFile?: (path: string) => void
}): JSX.Element {
  const name = box.path.split('/').pop() ?? box.path
  const thin = box.specCovered < box.specTotal

  if (lod === 'file') {
    // In user units, because the canvas is: 12 client pixels at 40% zoom is 30
    // of them. This is the whole reason the far view can be read at all.
    const label = FILE_LABEL_PX * labelScale
    const meta = FILE_META_PX * labelScale
    return (
      <g
        transform={`translate(${box.x} ${box.y})`}
        onClick={() => onPickFile?.(box.path)}
        className="cursor-pointer"
      >
        <rect
          width={box.width}
          height={box.height}
          rx={3}
          fill="var(--color-panel)"
          stroke={hot ? 'var(--color-line-strong)' : 'var(--color-line)'}
        />
        <text
          x={8}
          y={8 + label}
          fontSize={label}
          fontFamily="var(--font-mono)"
          fill="var(--color-fg)"
        >
          {truncate(name, fitChars(box.width, label))}
        </text>
        <text
          x={8}
          y={FILE_BOX_H - 8}
          fontSize={meta}
          fontFamily="var(--font-mono)"
          fill={thin ? 'var(--color-warn)' : 'var(--color-fg-mute)'}
        >
          {box.total} fn · {box.specCovered}/{box.specTotal}
        </text>
        <title>{box.path}</title>
      </g>
    )
  }

  return (
    <g transform={`translate(${box.x} ${box.y})`}>
      <rect
        width={box.width}
        height={box.height}
        rx={3}
        fill="var(--color-panel)"
        stroke={hot ? 'var(--color-line-strong)' : 'var(--color-line)'}
      />
      <text x={8} y={19} fontSize={11} fontFamily="var(--font-mono)" fill="var(--color-fg)">
        {truncate(name, 26)}
      </text>
      <text
        x={box.width - 8}
        y={19}
        fontSize={9}
        textAnchor="end"
        fontFamily="var(--font-mono)"
        fill={thin ? 'var(--color-warn)' : 'var(--color-fg-mute)'}
      >
        {box.total} fn · {box.specCovered}/{box.specTotal}
      </text>
      <title>{box.path}</title>
      <line
        x1={0}
        y1={HEADER_H - 5}
        x2={box.width}
        y2={HEADER_H - 5}
        stroke="var(--color-line)"
      />

      {box.functions.map((fn, row) => {
        const y = HEADER_H + row * ROW_H - 5
        const selected = fn.id === selectedId
        const mark = selected ? 'selected' : marks.get(fn.id)
        return (
          <g
            key={fn.id}
            data-fn={fn.id}
            transform={`translate(0 ${y})`}
            // A double-click arrives as click, click, dblclick — and `onSelect`
            // unfocuses a row that is already the selection. Left alone, the
            // second click would undo the first and the viewer would open with
            // nothing selected. `detail > 1` is that second click, ignored.
            onClick={(event) => {
              if (event.detail > 1) return
              onSelect(fn.id)
            }}
            onDoubleClick={() => onOpenRow(fn)}
            className="cursor-pointer"
          >
            <rect
              x={1}
              y={0}
              width={box.width - 2}
              height={ROW_H}
              fill={
                selected
                  ? 'var(--color-accent-soft)'
                  : mark === 'hover'
                    ? 'var(--color-hover)'
                    : 'transparent'
              }
              className={selected ? '' : 'hover:fill-hover'}
            />
            {mark === 'both' ? (
              <>
                <rect x={1} y={0} width={2} height={ROW_H / 2} fill={EDGE_STYLES.calls.stroke} />
                <rect
                  x={1}
                  y={ROW_H / 2}
                  width={2}
                  height={ROW_H / 2}
                  fill={EDGE_STYLES.callers.stroke}
                />
              </>
            ) : mark ? (
              <rect x={1} y={0} width={2} height={ROW_H} fill={markColour(mark)} />
            ) : null}
            <text
              x={10}
              y={13}
              fontSize={10.5}
              fontFamily="var(--font-mono)"
              // 명세 없는 함수는 회색 — coverage you can see.
              fill={
                selected
                  ? 'var(--color-fg)'
                  : fn.hasSpec
                    ? 'var(--color-fg-dim)'
                    : 'var(--color-fg-mute)'
              }
            >
              {fn.isTest ? 't ' : ''}
              {truncate(fn.name, 34)}
            </text>
            {fn.hasSpec ? (
              <circle cx={box.width - 8} cy={ROW_H / 2} r={1.8} fill="var(--color-ok)" />
            ) : null}
            <title>
              {fn.qualname}
              {fn.summary ? ` — ${fn.summary}` : ' — no spec'}
            </title>
          </g>
        )
      })}

      {/* A cut list must never read as the whole file. Not a button: turning
          rows back on one box at a time is the next slice, not this one. */}
      {box.hidden > 0 ? (
        <text
          x={10}
          y={HEADER_H + box.functions.length * ROW_H + 8}
          fontSize={9}
          fontFamily="var(--font-mono)"
          fill="var(--color-fg-mute)"
        >
          +{box.hidden} more
        </text>
      ) : null}
    </g>
  )
})

interface DrawnEdge {
  key: string
  d: string
  fromId: number
  toId: number
  /** Is this someone calling the selection, rather than the selection calling? */
  incoming: boolean
  /** The engine would not commit to this target, so neither does the line. */
  faint: boolean
}

/**
 * The selected node's own edges, and only those.
 *
 * @param detail  the loaded node, or null
 * @param layout  the placement at this level of detail
 * @param byId    every function of the index, by id
 * @param byBox   how far each dragged box has been pulled, by box index
 * @param lod     what this zoom draws
 * @flow  the far view draws no function edges at all — that is what makes it
 *        readable ; otherwise each resolved call and each caller, recursion
 *        excluded — a call to yourself has no curve to draw, only a mark on its
 *        own row ; an end this level does not draw as a row attaches to its
 *        file's box instead, and every anchor is read where its box is now, so
 *        the curves follow a drag
 * 주요 내부 변수: seen(같은 쌍의 중복 엣지 제거)
 */
function edgesFor(
  detail: GraphNodeDetail | null,
  layout: GraphLayout,
  byId: Map<number, GraphFunction>,
  byBox: Map<number, Offset>,
  lod: Lod
): DrawnEdge[] {
  if (!detail || lod === 'file') return []
  const self = resolveAnchor(layout, byId, byBox, detail.fn.id)
  if (!self) return []

  const out: DrawnEdge[] = []
  const seen = new Set<string>()

  for (const call of detail.calls) {
    if (call.targetId === null || call.targetId === detail.fn.id) continue
    const key = `out-${call.targetId}`
    if (seen.has(key)) continue
    const target = resolveAnchor(layout, byId, byBox, call.targetId)
    if (!target) continue
    seen.add(key)
    out.push({
      key,
      d: edgePath(self, target),
      fromId: detail.fn.id,
      toId: call.targetId,
      incoming: false,
      faint: false
    })
  }

  for (const caller of detail.callers) {
    if (caller.callerId === detail.fn.id) continue
    const key = `in-${caller.callerId}`
    if (seen.has(key)) continue
    const source = resolveAnchor(layout, byId, byBox, caller.callerId)
    if (!source) continue
    seen.add(key)
    out.push({
      key,
      d: edgePath(source, self),
      fromId: caller.callerId,
      toId: detail.fn.id,
      incoming: true,
      faint: caller.unresolved
    })
  }

  return out
}

/**
 * What to say when the graph could not be read.
 *
 * @param index  the failed index result
 */
function problemTitle(index: GraphIndexResult): string {
  if (index.problem === 'no-graph') return 'No graph for this repository yet'
  if (index.problem === 'schema') return 'This graph was written by another schema'
  if (index.problem === 'no-sqlite') return 'This build cannot open SQLite'
  return 'The graph could not be read'
}

/**
 * `built_at` as a human reads a date, or the raw value when it is not one.
 *
 * @param iso  a meta timestamp
 */
function stamp(iso: string): string {
  if (!iso) return 'never'
  const at = Date.parse(iso)
  if (!Number.isFinite(at)) return iso
  const d = new Date(at)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`
}

/**
 * What the colours mean, how many file links are on screen, and which level.
 *
 * The cap is printed rather than hidden: a drawn subset that reads as the whole
 * graph would be the picture lying about the repository. The level is printed
 * for the same reason — rows that vanished with the zoom must say so, or the
 * graph is claiming this repository has fewer functions than it does.
 *
 * @param shown  how many file links are drawn
 * @param total  how many there are
 * @param lod    what this zoom draws
 * @param boxes  how many file boxes are placed
 * @param rows   how many function rows are placed
 * @param trace  the chain on screen and its drawing, or null when there is none
 * @flow  says "top N of M" only where the list was really cut ; a trace adds its
 *        own two segments, which are where every one of its counts is printed
 */
function Legend({
  shown,
  total,
  lod,
  boxes,
  rows,
  trace
}: {
  shown: number
  total: number
  lod: Lod
  boxes: number
  rows: number
  trace: { trace: GraphTrace; drawing: TraceDrawing } | null
}): JSX.Element {
  return (
    <div className="pointer-events-none absolute bottom-2 left-2 flex max-w-[calc(100%-1rem)] flex-wrap items-center gap-x-3 gap-y-1 rounded-sm border border-line bg-panel/90 px-2 py-1 text-micro text-fg-mute">
      <span className="flex items-center gap-1.5">
        <svg width="18" height="6" aria-hidden="true">
          <line x1="0" y1="3" x2="18" y2="3" stroke={EDGE_STYLES.calls.stroke} strokeWidth="1.4" />
        </svg>
        calls
      </span>
      <span className="flex items-center gap-1.5">
        <svg width="18" height="6" aria-hidden="true">
          <line
            x1="0"
            y1="3"
            x2="18"
            y2="3"
            stroke={EDGE_STYLES.callers.stroke}
            strokeWidth="1.4"
            strokeDasharray={EDGE_STYLES.callers.dash ?? undefined}
          />
        </svg>
        called by
      </span>
      <span className="flex items-center gap-1.5">
        <svg width="18" height="6" aria-hidden="true">
          <line x1="0" y1="3" x2="18" y2="3" stroke="var(--color-line-strong)" strokeWidth="3" />
        </svg>
        {shown < total ? `top ${shown} of ${total} file links` : `${total} file links`} · width =
        calls
      </span>
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-ok" aria-hidden="true" />
        has spec
      </span>
      <span className="text-fg-mute/70">grey = no spec</span>
      <span className="text-fg-dim">
        {lod === 'file'
          ? `files only · ${boxes} boxes`
          : lod === 'key'
            ? `top ${KEY_FN_PER_FILE} fn per file · ${rows} rows`
            : `all ${rows} fn`}
      </span>
      {trace ? <TraceLegend trace={trace.trace} drawing={trace.drawing} lod={lod} /> : null}
    </div>
  )
}

/**
 * The chain's own numbers: how far it went, how much of it is drawn, and both
 * kinds of thing it could not follow.
 *
 * The depth is printed even when nothing was cut, and that sentence is the point
 * of this whole component: a node at the last ring has no stub beside it, and
 * without this line the absence would read as "the chain ends here" instead of
 * "we stopped looking here".
 *
 * @param trace    the chain the DB answered with
 * @param drawing  where it landed on this canvas
 * @param lod      what this zoom draws
 * @flow  a cut curve list says "top N of M", cut nodes say how many were left
 *        out, breaks and external are counted apart because they mean different
 *        things ; the far view says the curves are gone rather than absent
 */
function TraceLegend({
  trace,
  drawing,
  lod
}: {
  trace: GraphTrace
  drawing: TraceDrawing
  lod: Lod
}): JSX.Element {
  const stops = trace.breaks.reduce((sum, stop) => sum + stop.count, 0)
  return (
    <>
      {stops > 0 ? (
        <span className="flex items-center gap-1.5">
          <svg width="18" height="6" aria-hidden="true">
            <line
              x1="0"
              y1="3"
              x2="18"
              y2="3"
              stroke={TRACE_BREAK_STYLE.stroke}
              strokeWidth="1.4"
              strokeDasharray={TRACE_BREAK_STYLE.dash ?? undefined}
            />
          </svg>
          trace stops here ({stops})
        </span>
      ) : null}
      <span className="text-fg-dim">
        trace: {trace.direction} · depth {trace.depth} — nothing past this ring was followed ·{' '}
        {trace.nodes.length} fn
        {trace.truncated > 0 ? ` (+${trace.truncated} not drawn)` : ''} ·{' '}
        {lod === 'file'
          ? 'files only — the chain shows as lit boxes'
          : drawing.shown < drawing.total
            ? `top ${drawing.shown} of ${drawing.total} calls`
            : `${drawing.total} calls`}
        {trace.external > 0 ? ` · ${trace.external} outside the repository` : ''}
      </span>
    </>
  )
}

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
import type {
  GraphEdge,
  GraphFileEdge,
  GraphFileGroup,
  GraphFunction,
  GraphIndexResult,
  GraphNodeDetail
} from '@domains/graph-view/types'
import {
  FILE_BOX_H,
  FILE_LABEL_PX,
  FILE_META_PX,
  HEADER_H,
  KEY_FN_PER_FILE,
  MAX_FILE_EDGES,
  NO_OFFSETS,
  ROW_H,
  ZOOM_DETAIL,
  boxCenter,
  buildAdjacency,
  callerCounts,
  canvasSize,
  centerScroll,
  clampOffset,
  edgePath,
  fileLines,
  fitChars,
  flattenFunctions,
  groupMarks,
  isDrag,
  layoutGraph,
  lodFor,
  matchFunctions,
  mergeMarks,
  nearestBoxPath,
  neighbourMarks,
  offsetOf,
  offsetsByBox,
  resolveAnchor,
  viewportOf,
  withOffset,
  type FileLine,
  type GraphBox,
  type GraphLayout,
  type Lod,
  type Offset,
  type Offsets,
  type RowMark
} from '@domains/graph-view/layout'
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
 *        -> otherwise the canvas, plus the detail panel for the selection,
 *        which folds away and comes back by itself when a row is picked
 * 주요 내부 변수: hover(포인터 아래의 행/파일), focus(선택 여부 = 뷰 모드),
 * offsets(끌어다 놓은 상자들 — 세션 한정), drag(진행 중인 드래그),
 * lod(이 줌이 그리는 레벨)
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
  const [query, setQuery] = useState('')
  const [hover, setHover] = useState<Hover>(NO_HOVER)
  const [offsets, setOffsets] = useState<Offsets>(NO_OFFSETS)
  const [detailOpen, setDetailOpen] = useState(true)
  const [mapOpen, setMapOpen] = useState(true)
  const canvas = useRef<HTMLDivElement>(null)

  // The drag itself is a ref, not state: a pointer move already re-renders
  // through `offsets`, and there is nothing to gain from a second one.
  const drag = useRef<Dragging | null>(null)
  /** Did the pointer sequence just ending actually move? A click reads this. */
  const dragged = useRef(false)
  const offsetsRef = useRef(offsets)
  offsetsRef.current = offsets

  /** The last selection this surface already answered by moving the scale. */
  const jumped = useRef<number | null>(null)
  /** The zoom and placement the current scroll position was measured against. */
  const view = useRef<{ zoom: number; layout: GraphLayout } | null>(null)
  /** A file to land on at the next placement — the far view's way in. */
  const pendingPath = useRef<string | null>(null)

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

  // Escape is the third way back to the whole view. The search box keeps its
  // own Escape — clearing what you typed comes before clearing the selection.
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return
      const target = event.target as HTMLElement | null
      if (target && target.tagName === 'INPUT') return
      onSelect(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onSelect])

  const edges = useMemo(
    () => edgesFor(detail, layout, byId, byBox, lod),
    [detail, layout, byId, byBox, lod]
  )

  // What the selection marks: itself, what it calls, what calls it. Read off
  // the drawn edges rather than the adjacency, so an unresolved call site is
  // marked in exactly the place it is drawn.
  const focusMarks = useMemo(() => {
    if (selected === null) return NO_MARKS
    const outs = new Map<number, RowMark>()
    const ins = new Map<number, RowMark>()
    for (const edge of edges) {
      if (edge.incoming) ins.set(edge.fromId, 'callers')
      else outs.set(edge.toId, 'calls')
    }
    const self = new Map<number, RowMark>([[selected, 'selected']])
    return mergeMarks(mergeMarks(outs, ins), self)
  }, [selected, edges])

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
   * A drag that ends on a row must not also select it.
   *
   * @param event  the click that follows the pointer being released
   * @flow  the capture phase is early enough to reach the row's own onClick
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

  // One listener per box instead of one per row: 1435 row listeners is 1435
  // closures rebuilt on every render, and the row is in the event anyway.
  const enter = useCallback((target: EventTarget | null, path: string): void => {
    // Hovering while dragging would set the marks flickering under the box the
    // pointer is carrying.
    if (drag.current) return
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
      />

      <div className="flex min-h-0 flex-1">
        {/* The legend and the minimap sit on a layer that does not scroll, so
            neither of them has to be pinned with a sticky trick. */}
        <div className="relative min-w-0 flex-1">
          <div ref={canvas} className="h-full w-full overflow-auto bg-app">
            <svg
              width={size.width * zoom}
              height={size.height * zoom}
              viewBox={`0 0 ${Math.max(1, size.width)} ${Math.max(1, size.height)}`}
              className="block select-none"
              onPointerDownCapture={clearDragFlag}
            >
              <defs>
                <marker
                  id="fn-arrow"
                  viewBox="0 0 8 8"
                  refX="7"
                  refY="4"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M1 1 L7 4 L1 7" fill="none" stroke="context-stroke" strokeWidth="1.2" />
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

              {/* Clicking bare canvas is the first way back to the whole view. */}
              <rect
                width={Math.max(1, size.width)}
                height={Math.max(1, size.height)}
                fill="transparent"
                onClick={clearSelection}
              />

              {/* LOD, level one: the whole view, one curve per file pair. It
                  survives every zoom — the far view keeps the file links and
                  drops only the function edges. */}
              <FileLinks lines={links.lines} hotPath={hover.path} dim={focus} />

              {/* LOD, level two: function edges exist only around a selection,
                  and only where this zoom draws function rows at all. */}
              <g>
                {edges.map((edge) => (
                  <path
                    key={edge.key}
                    d={edge.d}
                    fill="none"
                    stroke={edge.incoming ? 'var(--color-ok)' : 'var(--color-accent)'}
                    strokeWidth={1.4}
                    strokeDasharray={edge.incoming ? '3 3' : undefined}
                    strokeOpacity={edge.faint ? 0.55 : 1}
                    markerEnd="url(#fn-arrow)"
                  />
                ))}
              </g>

              {/* The drag lives on this wrapper, outside `FileBox`'s memo: only
                  one attribute changes, so the rows inside are not re-rendered
                  even once while the box is being carried across the canvas. */}
              {layout.boxes.map((box, i) => {
                const off = offsets.get(box.path)
                return (
                  <g
                    key={box.path}
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
              <FunctionDetail detail={detail} loading={detailLoading} onSelect={pick} />
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
  onReset
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
  onPickFile
}: {
  box: GraphBox
  lod: Lod
  labelScale: number
  selectedId: number | null
  marks: ReadonlyMap<number, RowMark>
  hot: boolean
  onSelect: (id: number) => void
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
            onClick={() => onSelect(fn.id)}
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
                <rect x={1} y={0} width={2} height={ROW_H / 2} fill="var(--color-accent)" />
                <rect x={1} y={ROW_H / 2} width={2} height={ROW_H / 2} fill="var(--color-ok)" />
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

/**
 * The colour one mark is drawn in — the same two the edges use.
 *
 * @param mark  what this row is to whatever the pointer is on
 * @flow  outgoing reads accent, incoming reads ok, the rest are neutral
 */
function markColour(mark: RowMark): string {
  if (mark === 'selected' || mark === 'calls') return 'var(--color-accent)'
  if (mark === 'callers') return 'var(--color-ok)'
  if (mark === 'hover') return 'var(--color-fg-dim)'
  return 'var(--color-fg-mute)'
}

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
 * @flow  says "top N of M" only where the list was really cut
 */
function Legend({
  shown,
  total,
  lod,
  boxes,
  rows
}: {
  shown: number
  total: number
  lod: Lod
  boxes: number
  rows: number
}): JSX.Element {
  return (
    <div className="pointer-events-none absolute bottom-2 left-2 inline-flex items-center gap-3 rounded-sm border border-line bg-panel/90 px-2 py-1 text-micro text-fg-mute">
      <span className="flex items-center gap-1.5">
        <svg width="18" height="6" aria-hidden="true">
          <line x1="0" y1="3" x2="18" y2="3" stroke="var(--color-accent)" strokeWidth="1.4" />
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
            stroke="var(--color-ok)"
            strokeWidth="1.4"
            strokeDasharray="3 3"
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
    </div>
  )
}

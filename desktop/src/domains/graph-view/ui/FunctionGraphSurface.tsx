import { memo, useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react'
import type {
  GraphFileEdge,
  GraphFunction,
  GraphIndexResult,
  GraphNodeDetail
} from '@domains/graph-view/types'
import {
  BOX_W,
  HEADER_H,
  ROW_H,
  buildAdjacency,
  edgePath,
  fileLines,
  flattenFunctions,
  groupMarks,
  layoutGraph,
  matchFunctions,
  mergeMarks,
  neighbourMarks,
  type Anchor,
  type FileLine,
  type GraphBox,
  type RowMark
} from '@domains/graph-view/layout'
import { FunctionDetail } from '@domains/graph-view/ui/FunctionDetail'
import { EmptyState } from '@renderer/components/primitives'

/** A stable empty map, so a box with nothing marked keeps identical props. */
const NO_MARKS: Map<number, RowMark> = new Map()
const NO_FILE_EDGES: GraphFileEdge[] = []
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
 * **Selection draws, hover only lights up.** A selection is what makes edges
 * appear and dims everything unrelated; hovering merely brightens a row and its
 * direct neighbours. So a pointer crossing 1435 rows never makes a single path
 * appear or disappear, which is the difference between smooth and not.
 *
 * **Grey means no spec.** `has_spec = 0` renders muted, so coverage is
 * something you see rather than something you compute.
 *
 * @param index      the loaded index, or null while it loads
 * @param loading    is a read in flight?
 * @param selected   the focused function id, or null for the whole view
 * @param onSelect   focus another function, or null to return to the whole view
 * @param onBuild    run `aidev graph build`
 * @param busy       a command is already running (one slot)
 * @param zoom       the surface's zoom factor
 * @flow  no index -> a reading state ; index not ok -> the reason and a way out
 *        -> otherwise the canvas, plus the detail panel for the selection
 * 주요 내부 변수: hover(포인터 아래의 행/파일), focus(선택 여부 = 뷰 모드)
 */
export function FunctionGraphSurface({
  index,
  loading,
  selected,
  onSelect,
  onBuild,
  busy,
  zoom
}: {
  index: GraphIndexResult | null
  loading: boolean
  selected: number | null
  onSelect: (id: number | null) => void
  onBuild: () => void
  busy: boolean
  zoom: number
}): JSX.Element {
  const [detail, setDetail] = useState<GraphNodeDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [query, setQuery] = useState('')
  const [hover, setHover] = useState<Hover>(NO_HOVER)
  const canvas = useRef<HTMLDivElement>(null)

  const files = index?.ok ? index.files : []
  const layout = useMemo(() => layoutGraph(files), [files])
  const functions = useMemo(() => flattenFunctions(files), [files])
  const results = useMemo(() => matchFunctions(functions, query), [functions, query])

  const fileEdges = index?.ok ? index.fileEdges : NO_FILE_EDGES
  const links = useMemo(() => fileLines(fileEdges, layout), [fileEdges, layout])
  // Folded once from the index, not queried per hover: at 1435 rows, one round
  // trip per row the pointer crosses is exactly the lag being avoided here.
  const adjacency = useMemo(() => buildAdjacency(index?.ok ? index.edges : []), [index])

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

  // Bring the selection into view wherever it came from — a click, a search
  // hit, a caller in the panel, or the file list on the left.
  useEffect(() => {
    const box = canvas.current
    const anchor = selected === null ? undefined : layout.anchors.get(selected)
    if (!box || !anchor) return
    box.scrollTo({
      left: Math.max(0, anchor.left * zoom - box.clientWidth / 2 + (BOX_W * zoom) / 2),
      top: Math.max(0, anchor.y * zoom - box.clientHeight / 2),
      behavior: 'smooth'
    })
  }, [selected, layout, zoom])

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

  const edges = useMemo(() => edgesFor(detail, layout.anchors), [detail, layout])

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
      if (anchor) set.add(anchor.boxIndex)
    }
    return set
  }, [focusMarks, layout])

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

  // One listener per box instead of one per row: 1435 row listeners is 1435
  // closures rebuilt on every render, and the row is in the event anyway.
  const enter = useCallback((target: EventTarget | null, path: string): void => {
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
      />

      <div className="flex min-h-0 flex-1">
        <div ref={canvas} className="relative min-w-0 flex-1 overflow-auto bg-app">
          <svg
            width={layout.width * zoom}
            height={layout.height * zoom}
            viewBox={`0 0 ${Math.max(1, layout.width)} ${Math.max(1, layout.height)}`}
            className="block"
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
              width={Math.max(1, layout.width)}
              height={Math.max(1, layout.height)}
              fill="transparent"
              onClick={() => onSelect(null)}
            />

            {/* LOD, level one: the whole view, one curve per file pair. */}
            <FileLinks lines={links.lines} hotPath={hover.path} dim={focus} />

            {/* LOD, level two: function edges exist only around a selection. */}
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

            {layout.boxes.map((box, i) => (
              <g
                key={box.path}
                opacity={focus && !focusBoxes.has(i) ? BOX_DIM : 1}
                style={{ transition: 'opacity 90ms linear' }}
                onMouseOver={(event) => enter(event.target, box.path)}
                onMouseLeave={leave}
              >
                <FileBox
                  box={box}
                  selectedId={i === selectedBox ? selected : null}
                  marks={marks.get(i) ?? NO_MARKS}
                  hot={hotBoxes.has(i)}
                  onSelect={pickRow}
                />
              </g>
            ))}
          </svg>

          <Legend shown={links.shown} total={links.total} />
        </div>

        <div className="w-80 shrink-0 border-l border-line bg-panel">
          <FunctionDetail detail={detail} loading={detailLoading} onSelect={pick} />
        </div>
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
 * @flow  meta -> the commit/branch/times line, with dirty and stale called out
 */
function FreshnessBar({
  index,
  query,
  onQuery,
  results,
  onPick,
  onBuild,
  busy,
  loading
}: {
  index: GraphIndexResult
  query: string
  onQuery: (value: string) => void
  results: GraphFunction[]
  onPick: (id: number) => void
  onBuild: () => void
  busy: boolean
  loading: boolean
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
 * @param selectedId  the selection, when it is one of this box's rows
 * @param marks       what each of this box's marked rows is to the pointer
 * @param hot         does the hovered file link to this one?
 * @param onSelect    focus a row, or unfocus it when it is already the one
 * @flow  the header, then one row per function, each drawn by its mark
 */
const FileBox = memo(function FileBox({
  box,
  selectedId,
  marks,
  hot,
  onSelect
}: {
  box: GraphBox
  selectedId: number | null
  marks: ReadonlyMap<number, RowMark>
  hot: boolean
  onSelect: (id: number) => void
}): JSX.Element {
  const name = box.path.split('/').pop() ?? box.path
  const covered = box.functions.filter((fn) => !fn.isTest && fn.hasSpec).length
  const total = box.functions.filter((fn) => !fn.isTest).length

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
        fill={covered < total ? 'var(--color-warn)' : 'var(--color-fg-mute)'}
      >
        {box.functions.length} fn · {covered}/{total}
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
 * @param detail   the loaded node, or null
 * @param anchors  where every row sits
 * @flow  no selection -> none ; each resolved call and each caller both ends of
 *        which are actually on the canvas, recursion excluded — a call to
 *        yourself has no curve to draw, only a mark on its own row
 * 주요 내부 변수: seen(같은 쌍의 중복 엣지 제거)
 */
function edgesFor(detail: GraphNodeDetail | null, anchors: Map<number, Anchor>): DrawnEdge[] {
  if (!detail) return []
  const self = anchors.get(detail.fn.id)
  if (!self) return []

  const out: DrawnEdge[] = []
  const seen = new Set<string>()

  for (const call of detail.calls) {
    if (call.targetId === null || call.targetId === detail.fn.id) continue
    const target = anchors.get(call.targetId)
    const key = `out-${call.targetId}`
    if (!target || seen.has(key)) continue
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
    const source = anchors.get(caller.callerId)
    const key = `in-${caller.callerId}`
    if (!source || seen.has(key)) continue
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
 * What the colours mean, and how many file links are actually on screen.
 *
 * The cap is printed rather than hidden: a drawn subset that reads as the whole
 * graph would be the picture lying about the repository.
 *
 * @param shown  how many file links are drawn
 * @param total  how many there are
 * @flow  says "top N of M" only where the list was really cut
 */
function Legend({ shown, total }: { shown: number; total: number }): JSX.Element {
  return (
    <div className="pointer-events-none sticky bottom-2 left-2 ml-2 inline-flex items-center gap-3 rounded-sm border border-line bg-panel/90 px-2 py-1 text-micro text-fg-mute">
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
    </div>
  )
}

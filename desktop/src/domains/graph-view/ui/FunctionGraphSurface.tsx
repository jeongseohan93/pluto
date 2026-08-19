import { memo, useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react'
import type { GraphFunction, GraphIndexResult, GraphNodeDetail } from '@domains/graph-view/types'
import {
  BOX_W,
  HEADER_H,
  ROW_H,
  edgePath,
  flattenFunctions,
  layoutGraph,
  matchFunctions,
  type Anchor,
  type GraphBox
} from '@domains/graph-view/layout'
import { FunctionDetail } from '@domains/graph-view/ui/FunctionDetail'
import { EmptyState } from '@renderer/components/primitives'

/** A stable empty array, so a box with no highlight keeps identical props. */
const NO_IDS: number[] = []

/**
 * B — the Function DB on screen: files as boxes, functions as rows.
 *
 * **Why there are almost no edges.** This repository's graph holds 8669 call
 * edges. Drawing them at once is not a performance problem so much as a
 * legibility one — it is a hairball, and a hairball answers no question. So the
 * rule is LOD: edges exist only for the selected node's own neighbours. That
 * single decision is also what lets the layout be arithmetic (files in path
 * order, packed into columns) instead of a physics simulation, which is why
 * this slice adds no rendering dependency at all.
 *
 * **Grey means no spec.** `has_spec = 0` renders muted, so coverage is
 * something you see rather than something you compute.
 *
 * @param index      the loaded index, or null while it loads
 * @param loading    is a read in flight?
 * @param selected   the focused function id
 * @param onSelect   focus another function
 * @param onBuild    run `aidev graph build`
 * @param busy       a command is already running (one slot)
 * @param zoom       the surface's zoom factor
 * @flow  no index -> a reading state ; index not ok -> the reason and a way out
 *        -> otherwise the canvas, plus the detail panel for the selection
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
  onSelect: (id: number) => void
  onBuild: () => void
  busy: boolean
  zoom: number
}): JSX.Element {
  const [detail, setDetail] = useState<GraphNodeDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [query, setQuery] = useState('')
  const canvas = useRef<HTMLDivElement>(null)

  const files = index?.ok ? index.files : []
  const layout = useMemo(() => layoutGraph(files), [files])
  const functions = useMemo(() => flattenFunctions(files), [files])
  const results = useMemo(() => matchFunctions(functions, query), [functions, query])

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

  const edges = useMemo(() => edgesFor(detail, layout.anchors), [detail, layout])

  // Which rows each box has to draw as a neighbour. Boxes that hold none get
  // the same empty array every time, so `memo` skips them on every selection.
  const highlights = useMemo(() => {
    const map = new Map<number, number[]>()
    for (const edge of edges) {
      for (const id of [edge.fromId, edge.toId]) {
        const anchor = layout.anchors.get(id)
        if (!anchor) continue
        const bucket = map.get(anchor.boxIndex)
        if (bucket) {
          if (!bucket.includes(id)) bucket.push(id)
        } else map.set(anchor.boxIndex, [id])
      }
    }
    return map
  }, [edges, layout])

  const selectedBox = selected === null ? -1 : (layout.anchors.get(selected)?.boxIndex ?? -1)
  const pick = useCallback(
    (id: number) => {
      setQuery('')
      onSelect(id)
    },
    [onSelect]
  )

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
            </defs>

            {/* LOD: only the selection's own edges are ever drawn. */}
            <g>
              {edges.map((edge) => (
                <path
                  key={edge.key}
                  d={edge.d}
                  fill="none"
                  stroke={edge.incoming ? 'var(--color-fg-mute)' : 'var(--color-accent)'}
                  strokeWidth={1.4}
                  strokeDasharray={edge.incoming ? '3 3' : undefined}
                  markerEnd="url(#fn-arrow)"
                />
              ))}
            </g>

            {layout.boxes.map((box, i) => (
              <FileBox
                key={box.path}
                box={box}
                selectedId={i === selectedBox ? selected : null}
                highlights={highlights.get(i) ?? NO_IDS}
                onSelect={pick}
              />
            ))}
          </svg>

          <Legend />
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
 * One file box and its function rows.
 *
 * Memoised, and given only ids that concern it: a selection somewhere else
 * leaves every prop of this box referentially identical, so it does not
 * re-render. That is what keeps 78 boxes and 1335 rows responsive to a click.
 *
 * @param box         the placed box
 * @param selectedId  the selection, when it is one of this box's rows
 * @param highlights  this box's rows that are neighbours of the selection
 * @param onSelect    focus a row
 */
const FileBox = memo(function FileBox({
  box,
  selectedId,
  highlights,
  onSelect
}: {
  box: GraphBox
  selectedId: number | null
  highlights: number[]
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
        stroke="var(--color-line)"
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
        const near = highlights.includes(fn.id)
        return (
          <g
            key={fn.id}
            transform={`translate(0 ${y})`}
            onClick={() => onSelect(fn.id)}
            className="cursor-pointer"
          >
            <rect
              x={1}
              y={0}
              width={box.width - 2}
              height={ROW_H}
              fill={selected ? 'var(--color-accent-soft)' : 'transparent'}
              className={selected ? '' : 'hover:fill-hover'}
            />
            {selected ? <rect x={1} y={0} width={2} height={ROW_H} fill="var(--color-accent)" /> : null}
            {!selected && near ? (
              <rect x={1} y={0} width={2} height={ROW_H} fill="var(--color-fg-mute)" />
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

interface DrawnEdge {
  key: string
  d: string
  fromId: number
  toId: number
  /** Is this someone calling the selection, rather than the selection calling? */
  incoming: boolean
}

/**
 * The selected node's own edges, and only those.
 *
 * @param detail   the loaded node, or null
 * @param anchors  where every row sits
 * @flow  no selection -> none ; each resolved call and each caller that both
 *        ends of which are actually on the canvas
 * 주요 내부 변수: seen(같은 쌍의 중복 엣지 제거)
 */
function edgesFor(detail: GraphNodeDetail | null, anchors: Map<number, Anchor>): DrawnEdge[] {
  if (!detail) return []
  const self = anchors.get(detail.fn.id)
  if (!self) return []

  const out: DrawnEdge[] = []
  const seen = new Set<string>()

  for (const call of detail.calls) {
    if (call.targetId === null) continue
    const target = anchors.get(call.targetId)
    const key = `out-${call.targetId}`
    if (!target || seen.has(key)) continue
    seen.add(key)
    out.push({
      key,
      d: edgePath(self, target),
      fromId: detail.fn.id,
      toId: call.targetId,
      incoming: false
    })
  }

  for (const caller of detail.callers) {
    const source = anchors.get(caller.callerId)
    const key = `in-${caller.callerId}`
    if (!source || seen.has(key)) continue
    seen.add(key)
    out.push({
      key,
      d: edgePath(source, self),
      fromId: caller.callerId,
      toId: detail.fn.id,
      incoming: true
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

function Legend(): JSX.Element {
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
            stroke="var(--color-fg-mute)"
            strokeWidth="1.4"
            strokeDasharray="3 3"
          />
        </svg>
        called by
      </span>
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-ok" aria-hidden="true" />
        has spec
      </span>
      <span className="text-fg-mute/70">grey = no spec</span>
    </div>
  )
}

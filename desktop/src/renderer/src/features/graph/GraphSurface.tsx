import { useMemo, type JSX } from 'react'
import type { CodeGraph, GraphFileBox } from '@shared/ide'
import { EmptyState } from '@renderer/components/primitives'

/**
 * v0.0.1 graph placeholder.
 *
 * This draws authored coordinates from the mock — there is no layout engine and
 * no renderer abstraction here on purpose. Its job is to settle the visual
 * language (files contain symbols, edges are labelled relationships, scope is
 * legible at a glance) before v0.0.4 builds the real graph from project data.
 */
const HEADER_H = 26
const ROW_H = 22
const BOX_PAD = 6

function boxHeight(file: GraphFileBox): number {
  return HEADER_H + file.symbols.length * ROW_H + BOX_PAD
}

interface Anchor {
  left: number
  right: number
  y: number
}

function anchors(graph: CodeGraph): Map<string, Anchor> {
  const map = new Map<string, Anchor>()
  for (const file of graph.files) {
    file.symbols.forEach((symbol, row) => {
      map.set(symbol.id, {
        left: file.x,
        right: file.x + file.width,
        y: file.y + HEADER_H + row * ROW_H + ROW_H / 2
      })
    })
  }
  return map
}

function edgePath(from: Anchor, to: Anchor): string {
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

export function GraphSurface({
  graph,
  selectedSymbolId,
  onSelectSymbol,
  zoom
}: {
  graph: CodeGraph | null
  selectedSymbolId: string | null
  onSelectSymbol: (id: string) => void
  zoom: number
}): JSX.Element {
  const anchorMap = useMemo(() => (graph ? anchors(graph) : new Map<string, Anchor>()), [graph])

  if (!graph) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-tiny text-fg-mute">Resolving graph…</span>
      </div>
    )
  }

  if (graph.files.length === 0) {
    return (
      <EmptyState
        title="No graph for this workspace"
        hint="A code graph appears once a requirement has been scoped to files and symbols."
      />
    )
  }

  const incident = new Set(
    graph.edges
      .filter((e) => e.from === selectedSymbolId || e.to === selectedSymbolId)
      .map((e) => e.id)
  )

  return (
    <div className="relative h-full overflow-auto bg-app">
      <div className="flex min-h-full min-w-full items-center justify-center p-3">
        <svg
          width={graph.width * zoom}
          height={graph.height * zoom}
          viewBox={`0 0 ${graph.width} ${graph.height}`}
          className="block shrink-0"
        >
          <defs>
            <marker
              id="arrow"
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

          {/* Edges sit under the boxes so relationships never obscure names. */}
          <g>
            {graph.edges.map((edge) => {
              const from = anchorMap.get(edge.from)
              const to = anchorMap.get(edge.to)
              if (!from || !to) return null
              const active = incident.has(edge.id)
              const dim = selectedSymbolId !== null && !active
              const stroke = active
                ? 'var(--color-accent)'
                : edge.kind === 'tested-by'
                  ? 'var(--color-fg-mute)'
                  : 'var(--color-line-strong)'
              return (
                <path
                  key={edge.id}
                  d={edgePath(from, to)}
                  fill="none"
                  stroke={stroke}
                  strokeWidth={active ? 1.6 : 1.1}
                  strokeDasharray={edge.kind === 'tested-by' ? '3 3' : undefined}
                  markerEnd="url(#arrow)"
                  opacity={dim ? 0.35 : 1}
                />
              )
            })}
          </g>

          {graph.files.map((file) => (
            <FileBox
              key={file.id}
              file={file}
              selectedSymbolId={selectedSymbolId}
              onSelectSymbol={onSelectSymbol}
            />
          ))}
        </svg>
      </div>

      <Legend />
    </div>
  )
}

function FileBox({
  file,
  selectedSymbolId,
  onSelectSymbol
}: {
  file: GraphFileBox
  selectedSymbolId: string | null
  onSelectSymbol: (id: string) => void
}): JSX.Element {
  const height = boxHeight(file)
  const name = file.path.split('/').pop() ?? file.path
  const dir = file.path.split('/').slice(0, -1).join('/')

  return (
    <g transform={`translate(${file.x} ${file.y})`}>
      <rect
        width={file.width}
        height={height}
        rx={3}
        fill="var(--color-panel)"
        stroke={file.scope === 'focus' ? 'var(--color-line-strong)' : 'var(--color-line)'}
      />
      {/* Changed files carry a solid edge marker, not just a colour shift. */}
      {file.changed ? (
        <rect x={0} y={0} width={2} height={height} rx={1} fill="var(--color-warn)" />
      ) : null}

      <text
        x={10}
        y={17}
        fontSize={11}
        fontFamily="var(--font-mono)"
        fill={file.scope === 'context' ? 'var(--color-fg-mute)' : 'var(--color-fg)'}
      >
        {name}
      </text>
      <title>{dir ? `${dir}/${name}` : name}</title>
      <line x1={0} y1={HEADER_H - 4} x2={file.width} y2={HEADER_H - 4} stroke="var(--color-line)" />

      {file.symbols.map((symbol, row) => {
        const y = HEADER_H + row * ROW_H - 4
        const selected = symbol.id === selectedSymbolId
        return (
          <g
            key={symbol.id}
            data-symbol={symbol.id}
            transform={`translate(0 ${y})`}
            onClick={() => onSelectSymbol(symbol.id)}
            className="cursor-pointer"
          >
            <rect
              x={1}
              y={0}
              width={file.width - 2}
              height={ROW_H}
              fill={selected ? 'var(--color-accent-soft)' : 'transparent'}
              className={selected ? '' : 'hover:fill-hover'}
            />
            {selected ? (
              <rect x={1} y={0} width={2} height={ROW_H} fill="var(--color-accent)" />
            ) : null}
            <text
              x={12}
              y={15}
              fontSize={11}
              fontFamily="var(--font-mono)"
              fill={selected ? 'var(--color-fg)' : 'var(--color-fg-dim)'}
            >
              {truncate(symbol.name, Math.floor((file.width - 40) / 6.2))}
            </text>
            {symbol.changed ? (
              <text
                x={file.width - 12}
                y={15}
                fontSize={9}
                textAnchor="end"
                fontFamily="var(--font-mono)"
                fill="var(--color-warn)"
              >
                M
              </text>
            ) : null}
            <title>{symbol.signature}</title>
          </g>
        )
      })}
    </g>
  )
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`
}

function Legend(): JSX.Element {
  return (
    <div className="pointer-events-none absolute bottom-2 left-2 flex items-center gap-3 rounded-sm border border-line bg-panel/90 px-2 py-1 text-micro text-fg-mute">
      <span className="flex items-center gap-1.5">
        <svg width="18" height="6" aria-hidden="true">
          <line x1="0" y1="3" x2="18" y2="3" stroke="var(--color-line-strong)" strokeWidth="1.2" />
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
            strokeWidth="1.2"
            strokeDasharray="3 3"
          />
        </svg>
        tested by
      </span>
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-2.5 w-[2px] bg-warn" aria-hidden="true" />
        changed
      </span>
    </div>
  )
}

import type { JSX } from 'react'
import type { ChangeSummary, CodeGraph, TestCase } from '@shared/ide'
import { TabBar } from '@renderer/components/primitives'
import { GraphSurface } from '@renderer/features/graph/GraphSurface'
import { CodeSurface } from '@renderer/features/code/CodeSurface'
import { TestSurface } from '@renderer/features/tests/TestSurface'
import { DiffSurface } from '@renderer/features/diff/DiffSurface'
import { BrowserSurface } from '@renderer/features/browser/BrowserSurface'
import { PlanSurface, type PlanSurfaceProps } from '@renderer/features/pipeline/PlanSurface'
import type { SymbolLocation } from '@renderer/lib/graph-lookup'
import { Icon } from '@renderer/components/Icon'
import { DemoBadge } from '@shared/ui/DemoBadge'
import { FunctionGraphSurface } from '@domains/graph-view/ui/FunctionGraphSurface'
import type { GraphIndexResult } from '@domains/graph-view/types'

export type SurfaceId = 'plan' | 'functions' | 'graph' | 'code' | 'test' | 'diff' | 'browser'

export interface SurfaceData {
  /** The real pipeline. */
  pipeline: PlanSurfaceProps
  /** The real Function DB. */
  functions: {
    index: GraphIndexResult | null
    loading: boolean
    selected: number | null
    onSelect: (id: number) => void
    onBuild: () => void
    busy: boolean
  }
  waitingCount: number
  /** Mock from here down — every surface that shows these wears a [DEMO] badge. */
  graph: CodeGraph | null
  changes: ChangeSummary | null
  tests: TestCase[] | null
  location: SymbolLocation | null
  selectedSymbolId: string | null
  onSelectSymbol: (id: string) => void
}

/**
 * One work surface. The shell holds several of these side by side, so nothing
 * here assumes it owns the whole window.
 */
export function SurfacePane({
  surface,
  onSurfaceChange,
  data,
  zoom,
  onZoomChange,
  onToggleSplit,
  splitOpen
}: {
  surface: SurfaceId
  onSurfaceChange: (id: SurfaceId) => void
  data: SurfaceData
  zoom: number
  onZoomChange: (z: number) => void
  onToggleSplit?: () => void
  splitOpen?: boolean
}): JSX.Element {
  const failed = (data.tests ?? []).filter((t) => t.state === 'failed').length

  const tabs = [
    {
      id: 'plan' as const,
      label: 'Plan',
      badge:
        data.waitingCount > 0 ? (
          <span className="rounded-sm bg-accent-soft px-1 font-mono text-micro text-accent">
            {data.waitingCount}
          </span>
        ) : undefined
    },
    { id: 'functions' as const, label: 'Function graph' },
    { id: 'graph' as const, label: 'Graph', badge: <DemoBadge /> },
    { id: 'code' as const, label: 'Code', badge: <DemoBadge /> },
    {
      id: 'test' as const,
      label: 'Test',
      badge: (
        <>
          {failed > 0 ? (
            <span className="rounded-sm bg-bad/20 px-1 font-mono text-micro text-bad">{failed}</span>
          ) : null}
          <DemoBadge />
        </>
      )
    },
    {
      id: 'diff' as const,
      label: 'Diff',
      badge: (
        <>
          {data.changes?.filesChanged ? (
            <span className="rounded-sm bg-active px-1 font-mono text-micro text-fg-mute">
              {data.changes.filesChanged}
            </span>
          ) : null}
          <DemoBadge />
        </>
      )
    },
    { id: 'browser' as const, label: 'Browser', badge: <DemoBadge /> }
  ]

  return (
    <div className="flex h-full min-w-0 flex-col bg-raised">
      <TabBar
        tabs={tabs}
        value={surface}
        onChange={onSurfaceChange}
        right={
          <>
            {surface === 'graph' || surface === 'functions' ? (
              <div className="flex items-center gap-0.5 font-mono text-micro text-fg-mute">
                <ZoomButton label="−" onClick={() => onZoomChange(Math.max(0.6, zoom - 0.2))} />
                <span className="w-9 text-center">{Math.round(zoom * 100)}%</span>
                <ZoomButton label="+" onClick={() => onZoomChange(Math.min(1.8, zoom + 0.2))} />
              </div>
            ) : null}
            {onToggleSplit ? (
              <button
                type="button"
                onClick={onToggleSplit}
                title={splitOpen ? 'Close split' : 'Split editor'}
                aria-pressed={splitOpen}
                className={`p-1 ${splitOpen ? 'text-fg-dim' : 'text-fg-mute hover:text-fg-dim'}`}
              >
                <Icon name="split" size={14} />
              </button>
            ) : null}
          </>
        }
      />

      <div className="min-h-0 flex-1">
        {surface === 'plan' ? (
          <PlanSurface {...data.pipeline} />
        ) : surface === 'functions' ? (
          <FunctionGraphSurface
            index={data.functions.index}
            loading={data.functions.loading}
            selected={data.functions.selected}
            onSelect={data.functions.onSelect}
            onBuild={data.functions.onBuild}
            busy={data.functions.busy}
            zoom={zoom}
          />
        ) : surface === 'graph' ? (
          <GraphSurface
            graph={data.graph}
            selectedSymbolId={data.selectedSymbolId}
            onSelectSymbol={data.onSelectSymbol}
            zoom={zoom}
          />
        ) : surface === 'code' ? (
          <CodeSurface location={data.location} />
        ) : surface === 'test' ? (
          <TestSurface tests={data.tests} selectedSymbolName={data.location?.symbol.name ?? null} />
        ) : surface === 'diff' ? (
          <DiffSurface changes={data.changes} />
        ) : (
          <BrowserSurface />
        )}
      </div>
    </div>
  )
}

function ZoomButton({ label, onClick }: { label: string; onClick: () => void }): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className="h-5 w-5 rounded-sm hover:bg-hover hover:text-fg-dim"
    >
      {label}
    </button>
  )
}

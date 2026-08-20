import type { JSX } from 'react'
import { TabBar } from '@renderer/components/primitives'
import { PlanSurface, type PlanSurfaceProps } from '@renderer/features/pipeline/PlanSurface'
import { Icon } from '@renderer/components/Icon'
import { FunctionGraphSurface } from '@domains/graph-view/ui/FunctionGraphSurface'
import { zoomIn, zoomOut } from '@domains/graph-view/layout'
import type { GraphIndexResult } from '@domains/graph-view/types'

/**
 * The surfaces the tab bar can open. The five mock screens
 * (`features/graph|code|tests|diff|browser`) are still in the tree, but nothing
 * routes to them any more: a tab bar that is half demo is a tab bar nobody can
 * read. They come back by being finished, not by being listed.
 */
export type SurfaceId = 'plan' | 'functions'

export interface SurfaceData {
  /** The real pipeline. */
  pipeline: PlanSurfaceProps
  /** The real Function DB. */
  functions: {
    index: GraphIndexResult | null
    loading: boolean
    selected: number | null
    /** null clears the selection — the canvas goes back to the whole view. */
    onSelect: (id: number | null) => void
    onBuild: () => void
    busy: boolean
  }
  waitingCount: number
}

/**
 * One work surface. The shell holds several of these side by side, so nothing
 * here assumes it owns the whole window.
 *
 * @param surface          which surface this pane is showing
 * @param onSurfaceChange  a tab was pressed
 * @param data             everything the two surfaces read
 * @param zoom             the graph's scale, shared by every pane
 * @param onZoomChange     change that scale
 * @param onToggleSplit    open or close the shell's second pane
 * @param splitOpen        is that second pane open?
 * @param codeSplit        the share the code panel takes beside the graph, held
 *                         by the shell so a trip to another tab does not lose it
 * @param onCodeSplitChange  the graph/code divider was dragged
 * @param onCodeSplitOpen  code appeared beside the graph — the shell's chance to
 *                         make room for it
 */
export function SurfacePane({
  surface,
  onSurfaceChange,
  data,
  zoom,
  onZoomChange,
  onToggleSplit,
  splitOpen,
  codeSplit,
  onCodeSplitChange,
  onCodeSplitOpen
}: {
  surface: SurfaceId
  onSurfaceChange: (id: SurfaceId) => void
  data: SurfaceData
  zoom: number
  onZoomChange: (z: number) => void
  onToggleSplit?: () => void
  splitOpen?: boolean
  codeSplit: number
  onCodeSplitChange: (fraction: number) => void
  onCodeSplitOpen?: () => void
}): JSX.Element {
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
    // Just 'Graph' now: with the mock one gone from the bar this is the only
    // graph in the app. The id stays 'functions' — what it opens is still the
    // Function DB, and inheriting the dead tab's id would blur which is which.
    { id: 'functions' as const, label: 'Graph' }
  ]

  return (
    <div className="flex h-full min-w-0 flex-col bg-raised">
      <TabBar
        tabs={tabs}
        value={surface}
        onChange={onSurfaceChange}
        right={
          <>
            {surface === 'functions' ? (
              // A ladder rather than ±0.2, so the two rungs below 60% — where
              // the function graph switches to file boxes — can be reached.
              <div className="flex items-center gap-0.5 font-mono text-micro text-fg-mute">
                <ZoomButton label="−" onClick={() => onZoomChange(zoomOut(zoom))} />
                <span className="w-9 text-center">{Math.round(zoom * 100)}%</span>
                <ZoomButton label="+" onClick={() => onZoomChange(zoomIn(zoom))} />
              </div>
            ) : null}
            {onToggleSplit ? (
              <button
                type="button"
                onClick={onToggleSplit}
                // "Split" now names the graph/code divider inside the surface,
                // so this button says what it actually opens: a second pane.
                title={splitOpen ? 'Close the second pane' : 'Open a second pane'}
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
        ) : (
          <FunctionGraphSurface
            index={data.functions.index}
            loading={data.functions.loading}
            selected={data.functions.selected}
            onSelect={data.functions.onSelect}
            onBuild={data.functions.onBuild}
            busy={data.functions.busy}
            zoom={zoom}
            onZoomChange={onZoomChange}
            split={codeSplit}
            onSplitChange={onCodeSplitChange}
            onSplitOpen={onCodeSplitOpen}
          />
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

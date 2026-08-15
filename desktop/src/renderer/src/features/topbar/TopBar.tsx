import type { JSX } from 'react'
import type { ProjectInfo, TelemetrySnapshot, WorkspaceSummary } from '@shared/ide'
import { Icon } from '@renderer/components/Icon'
import { RunStateBadge } from '@renderer/components/primitives'
import { tokens, usd } from '@renderer/lib/format'

/**
 * The top bar answers the first three questions in the information hierarchy:
 * which project, what is the agent doing, and what is that costing.
 */
export function TopBar({
  project,
  workspace,
  telemetry
}: {
  project: ProjectInfo
  workspace: WorkspaceSummary | undefined
  telemetry: TelemetrySnapshot
}): JSX.Element {
  return (
    <header className="flex h-9 shrink-0 items-center gap-3 border-b border-line bg-panel pr-2 pl-3">
      <span className="shrink-0 whitespace-nowrap text-tiny font-semibold tracking-[0.14em] text-fg-dim">
        AI DEV IDE
      </span>

      <span className="h-4 w-px shrink-0 bg-line" aria-hidden="true" />

      <button
        type="button"
        className="flex min-w-0 items-center gap-1.5 rounded-sm px-1.5 py-1 text-tiny hover:bg-hover"
      >
        <Icon name="folder" size={13} className="shrink-0 text-fg-mute" />
        <span className="truncate text-fg">{project.name}</span>
        <span className="truncate font-mono text-micro text-fg-mute">{project.branch}</span>
        <Icon name="chevron" size={11} className="shrink-0 rotate-90 text-fg-mute" />
      </button>

      {workspace ? (
        <div className="flex min-w-0 items-center gap-2">
          <Icon name="chevron" size={11} className="shrink-0 text-fg-mute" />
          <span className="truncate text-tiny text-fg">{workspace.title}</span>
          <span className="shrink-0 rounded-sm bg-active px-1.5 py-px font-mono text-micro text-fg-mute">
            {workspace.slice}
          </span>
        </div>
      ) : null}

      <div className="ml-auto flex shrink-0 items-center gap-3">
        <div className="flex items-center gap-2 rounded-sm bg-raised px-2 py-1">
          <span className="text-tiny text-fg-dim">{telemetry.agent}</span>
          <span className="font-mono text-micro text-fg-mute">{telemetry.model}</span>
          <RunStateBadge state={telemetry.state} />
        </div>

        {/* Live token / context stays on the main surface, not behind a menu. */}
        <dl className="flex items-center gap-3 font-mono text-micro">
          <Stat
            label="5h"
            value={`${telemetry.sessionUsagePct}%`}
            warn={telemetry.sessionUsagePct > 80}
          />
          <Stat label="ctx" value={tokens(telemetry.contextTokens)} />
          <Stat label="turns" value={String(telemetry.turns)} />
          <Stat label="out" value={tokens(telemetry.outputTokens)} />
          <Stat label="cost" value={usd(telemetry.costUsd)} />
        </dl>
      </div>
    </header>
  )
}

function Stat({
  label,
  value,
  warn = false
}: {
  label: string
  value: string
  warn?: boolean
}): JSX.Element {
  return (
    <div className="flex items-baseline gap-1">
      <dt className="text-fg-mute">{label}</dt>
      <dd className={warn ? 'text-warn' : 'text-fg-dim'}>{value}</dd>
    </div>
  )
}

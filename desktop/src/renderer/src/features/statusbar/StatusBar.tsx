import type { JSX } from 'react'
import type { ProjectInfo, TelemetrySnapshot, WorkspaceSummary } from '@shared/ide'
import { RunStateBadge } from '@renderer/components/primitives'
import { APPROVAL_TEXT } from '@renderer/lib/labels'
import { tokens } from '@renderer/lib/format'
import { DemoBadge } from '@shared/ui/DemoBadge'

/**
 * The status line. Everything on it except `notice` is still v0.0.1 mock, so it
 * wears the badge.
 *
 * @param project         the mock project
 * @param workspace       the mock workspace, if one is selected
 * @param telemetry       the mock telemetry
 * @param selectionLabel  what is selected in the demo graph
 * @param notice          a real refusal from the command slot, or null
 */
export function StatusBar({
  project,
  workspace,
  telemetry,
  selectionLabel,
  notice
}: {
  project: ProjectInfo
  workspace: WorkspaceSummary | undefined
  telemetry: TelemetrySnapshot
  selectionLabel: string | null
  notice: string | null
}): JSX.Element {
  return (
    <footer className="flex h-6 shrink-0 items-center gap-4 border-t border-line bg-panel px-3 text-micro text-fg-mute">
      {notice ? (
        <span className="min-w-0 truncate text-bad" title={notice}>
          {notice}
        </span>
      ) : null}
      <DemoBadge />
      <span className="font-mono">{project.branch}</span>
      <span>{project.dirtyFiles} uncommitted</span>

      {workspace ? (
        <>
          <span className="h-3 w-px bg-line" aria-hidden="true" />
          <RunStateBadge state={workspace.state} />
          <span>{APPROVAL_TEXT[workspace.approval]}</span>
          <span className={workspace.testsFailed > 0 ? 'text-bad' : 'text-ok'}>
            {workspace.testsPassed}/{workspace.testsPassed + workspace.testsFailed} tests
          </span>
        </>
      ) : null}

      {selectionLabel ? (
        <span className="min-w-0 truncate font-mono" title={selectionLabel}>
          {selectionLabel}
        </span>
      ) : null}

      <span className="ml-auto shrink-0 font-mono">
        ctx {tokens(telemetry.contextTokens)} · {telemetry.currentTool}
      </span>
    </footer>
  )
}

import type { JSX } from 'react'
import type { LogLine, SessionLogs, TelemetrySnapshot } from '@shared/ide'
import { Meter, TabBar } from '@renderer/components/primitives'
import { tokens, usd } from '@renderer/lib/format'

export type BottomTab = 'agent' | 'terminal' | 'test' | 'telemetry'

const TABS = [
  { id: 'agent', label: 'Agent' },
  { id: 'terminal', label: 'Terminal' },
  { id: 'test', label: 'Test' },
  { id: 'telemetry', label: 'Telemetry' }
] as const

export function BottomPanel({
  tab,
  onTabChange,
  logs,
  telemetry,
  collapsed,
  onToggleCollapsed
}: {
  tab: BottomTab
  onTabChange: (tab: BottomTab) => void
  logs: SessionLogs
  telemetry: TelemetrySnapshot
  collapsed: boolean
  onToggleCollapsed: () => void
}): JSX.Element {
  return (
    <section className="flex h-full min-h-0 flex-col bg-panel">
      <TabBar
        tabs={TABS}
        value={tab}
        onChange={onTabChange}
        right={
          <button
            type="button"
            onClick={onToggleCollapsed}
            className="px-1 text-micro text-fg-mute hover:text-fg-dim"
          >
            {collapsed ? 'expand' : 'collapse'}
          </button>
        }
      />
      {collapsed ? null : (
        <div className="min-h-0 flex-1 overflow-auto">
          {tab === 'telemetry' ? (
            <TelemetryDetail telemetry={telemetry} />
          ) : (
            <LogView
              lines={tab === 'agent' ? logs.agent : tab === 'terminal' ? logs.terminal : logs.test}
            />
          )}
        </div>
      )}
    </section>
  )
}

function LogView({ lines }: { lines: LogLine[] }): JSX.Element {
  const tone: Record<LogLine['level'], string> = {
    info: 'text-fg-dim',
    tool: 'text-fg',
    ok: 'text-ok',
    warn: 'text-warn',
    error: 'text-bad'
  }

  return (
    <ol className="py-1 font-mono text-tiny leading-[1.6]">
      {lines.map((line, i) => (
        <li key={i} className="flex gap-3 px-3 hover:bg-hover">
          <span className="shrink-0 select-none text-fg-mute">{line.ts}</span>
          <span className={`min-w-0 whitespace-pre-wrap break-words ${tone[line.level]}`}>
            {line.text}
          </span>
        </li>
      ))}
    </ol>
  )
}

function TelemetryDetail({ telemetry }: { telemetry: TelemetrySnapshot }): JSX.Element {
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(190px,1fr))] gap-x-6 gap-y-3 px-3 py-3">
      <Meter
        label="5h session usage"
        value={telemetry.sessionUsagePct}
        max={100}
        display={`${telemetry.sessionUsagePct}%`}
        tone={telemetry.sessionUsagePct > 80 ? 'warn' : 'accent'}
      />
      <Meter
        label="Context window"
        value={telemetry.contextTokens}
        max={telemetry.contextLimit}
        display={`${tokens(telemetry.contextTokens)} / ${tokens(telemetry.contextLimit)}`}
      />
      <Meter
        label="Peak context"
        value={telemetry.peakContextTokens}
        max={telemetry.contextLimit}
        display={tokens(telemetry.peakContextTokens)}
      />

      <dl className="col-span-full grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-x-6 border-t border-line pt-2 font-mono text-micro">
        {(
          [
            ['input', tokens(telemetry.inputTokens)],
            ['cache creation', tokens(telemetry.cacheCreationTokens)],
            ['cache read', tokens(telemetry.cacheReadTokens)],
            ['output', tokens(telemetry.outputTokens)],
            ['turns', String(telemetry.turns)],
            ['repeated reads', String(telemetry.repeatedReads)],
            ['current tool', telemetry.currentTool],
            ['current target', telemetry.currentTarget.split('/').pop() ?? ''],
            ['cost', usd(telemetry.costUsd)]
          ] as const
        ).map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3 py-[3px]">
            <dt className="shrink-0 text-fg-mute">{k}</dt>
            <dd className="min-w-0 truncate text-fg-dim" title={v}>
              {v}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

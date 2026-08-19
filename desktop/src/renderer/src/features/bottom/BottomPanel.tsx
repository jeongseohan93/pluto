import { useEffect, useRef, type JSX } from 'react'
import type { LogLine, SessionLogs, TelemetrySnapshot } from '@shared/ide'
import { Meter, TabBar } from '@renderer/components/primitives'
import { tokens, usd } from '@renderer/lib/format'
import { DemoBadge } from '@shared/ui/DemoBadge'
import type { CommandState } from '@domains/pipeline/types'

export type BottomTab = 'run' | 'agent' | 'terminal' | 'test' | 'telemetry'

/**
 * `run` is the only real one — it is the output of the process this app
 * launched. The other four are still v0.0.1 mock and say so.
 */
const TABS = [
  { id: 'run', label: 'Run' },
  { id: 'agent', label: 'Agent', badge: <DemoBadge /> },
  { id: 'terminal', label: 'Terminal', badge: <DemoBadge /> },
  { id: 'test', label: 'Test', badge: <DemoBadge /> },
  { id: 'telemetry', label: 'Telemetry', badge: <DemoBadge /> }
] as const

/**
 * The watching half of 관전: whatever the launched `aidev` is saying, live.
 *
 * @param tab                which tab is open
 * @param onTabChange        switch tabs
 * @param logs               the mock session logs (the other four tabs)
 * @param telemetry          the mock telemetry
 * @param collapsed          is the panel collapsed to its tab bar?
 * @param onToggleCollapsed  collapse / expand
 * @param command            the running or last-finished command, or null
 * @param onStop             end the running command
 * @flow  collapsed -> nothing but the tabs ; run -> the live log ; telemetry ->
 *        the meters ; otherwise one of the mock logs
 */
export function BottomPanel({
  tab,
  onTabChange,
  logs,
  telemetry,
  collapsed,
  onToggleCollapsed,
  command,
  onStop
}: {
  tab: BottomTab
  onTabChange: (tab: BottomTab) => void
  logs: SessionLogs
  telemetry: TelemetrySnapshot
  collapsed: boolean
  onToggleCollapsed: () => void
  command: CommandState | null
  onStop: () => void
}): JSX.Element {
  return (
    <section className="flex h-full min-h-0 flex-col bg-panel">
      <TabBar
        tabs={TABS}
        value={tab}
        onChange={onTabChange}
        right={
          <>
            {command?.running ? (
              <button
                type="button"
                onClick={onStop}
                title="SIGTERM — the same thing Ctrl-C does"
                className="rounded-sm border border-line px-1.5 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-bad"
              >
                Stop
              </button>
            ) : null}
            <button
              type="button"
              onClick={onToggleCollapsed}
              className="px-1 text-micro text-fg-mute hover:text-fg-dim"
            >
              {collapsed ? 'expand' : 'collapse'}
            </button>
          </>
        }
      />
      {collapsed ? null : tab === 'run' ? (
        <RunView command={command} />
      ) : (
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

/**
 * The launched command: what is running, and everything it has said.
 *
 * Scroll follows the tail only while the reader is already at the bottom —
 * yanking the view away from a line someone is reading is worse than falling
 * behind.
 *
 * @param command  the running or last-finished command, or null
 * @flow  nothing has run -> say how to start one ; else the header and the log
 * 주요 내부 변수: box(스크롤 컨테이너), stuck(바닥에 붙어 있었는가)
 */
function RunView({ command }: { command: CommandState | null }): JSX.Element {
  const box = useRef<HTMLDivElement>(null)
  const stuck = useRef(true)
  const seq = command?.seq ?? 0

  useEffect(() => {
    const node = box.current
    if (node && stuck.current) node.scrollTop = node.scrollHeight
  }, [seq])

  /** Remember whether the reader is at the tail, before the next line lands. */
  function onScroll(): void {
    const node = box.current
    if (node) stuck.current = node.scrollHeight - node.scrollTop - node.clientHeight < 24
  }

  if (!command) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <p className="text-tiny text-fg-mute">
          Nothing launched yet. Pick a requirement in the Pipeline panel and press Launch.
        </p>
      </div>
    )
  }

  return (
    <>
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-1">
        <span
          className={`shrink-0 font-mono text-micro ${command.running ? 'text-warn' : command.exitCode === 0 ? 'text-ok' : 'text-bad'}`}
        >
          {command.running
            ? 'RUNNING'
            : command.error
              ? 'STOPPED'
              : `EXIT ${command.exitCode ?? '?'}`}
        </span>
        <span
          className="min-w-0 flex-1 truncate font-mono text-micro text-fg-mute"
          title={command.argv.join(' ')}
        >
          {command.argv.join(' ')}
        </span>
        {command.running ? (
          <span
            className="shrink-0 text-micro text-fg-mute"
            title="the child process is tied to this window, the way it would be tied to a terminal"
          >
            runs while Pluto is open
          </span>
        ) : null}
      </div>
      <div ref={box} onScroll={onScroll} className="min-h-0 flex-1 overflow-auto">
        <LogView lines={command.lines} />
      </div>
    </>
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
          <span className={`min-w-0 whitespace-pre-wrap break-words select-text ${tone[line.level]}`}>
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

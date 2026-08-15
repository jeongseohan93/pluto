import type { JSX, ReactNode } from 'react'
import type { RunState } from '@shared/ide'

export function PanelHeader({
  title,
  right,
  className = ''
}: {
  title: string
  right?: ReactNode
  className?: string
}): JSX.Element {
  return (
    <div
      className={`flex h-7 shrink-0 items-center justify-between gap-2 border-b border-line bg-panel px-2.5 ${className}`}
    >
      <span className="panel-label truncate">{title}</span>
      {right ? <div className="flex shrink-0 items-center gap-1.5">{right}</div> : null}
    </div>
  )
}

export function TabBar<T extends string>({
  tabs,
  value,
  onChange,
  right
}: {
  tabs: readonly { id: T; label: string; badge?: ReactNode }[]
  value: T
  onChange: (id: T) => void
  right?: ReactNode
}): JSX.Element {
  return (
    <div className="flex h-8 shrink-0 items-stretch border-b border-line bg-panel">
      <div className="flex min-w-0 flex-1 items-stretch overflow-x-auto">
        {tabs.map((tab) => {
          const selected = tab.id === value
          return (
            <button
              key={tab.id}
              type="button"
              data-tab={tab.id}
              onClick={() => onChange(tab.id)}
              aria-selected={selected}
              className={`relative flex shrink-0 items-center gap-1.5 border-r border-line px-3 text-tiny transition-colors ${
                selected ? 'bg-raised text-fg' : 'text-fg-mute hover:bg-hover hover:text-fg-dim'
              }`}
            >
              {selected ? (
                <span className="absolute inset-x-0 top-0 h-px bg-accent" aria-hidden="true" />
              ) : null}
              {tab.label}
              {tab.badge}
            </button>
          )
        })}
      </div>
      {right ? <div className="flex shrink-0 items-center gap-1 px-2">{right}</div> : null}
    </div>
  )
}

export function EmptyState({
  title,
  hint,
  children
}: {
  title: string
  hint?: string
  children?: ReactNode
}): JSX.Element {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-1.5 px-6 text-center">
      <p className="text-small text-fg-dim">{title}</p>
      {hint ? <p className="max-w-[38ch] text-tiny text-fg-mute">{hint}</p> : null}
      {children}
    </div>
  )
}

const RUN_STATE_TEXT: Record<RunState, string> = {
  idle: 'IDLE',
  running: 'RUNNING',
  'awaiting-approval': 'NEEDS APPROVAL',
  passed: 'PASSED',
  failed: 'FAILED'
}

/**
 * Run state is never carried by colour alone — the label and the shape of the
 * marker both change, so it survives greyscale and colour-blind viewing.
 */
export function RunStateBadge({
  state,
  compact = false
}: {
  state: RunState
  compact?: boolean
}): JSX.Element {
  const tone: Record<RunState, string> = {
    idle: 'text-fg-mute',
    running: 'text-warn',
    'awaiting-approval': 'text-accent',
    passed: 'text-ok',
    failed: 'text-bad'
  }
  return (
    <span className={`flex items-center gap-1.5 text-micro font-semibold ${tone[state]}`}>
      <StateMark state={state} />
      {compact ? null : RUN_STATE_TEXT[state]}
    </span>
  )
}

function StateMark({ state }: { state: RunState }): JSX.Element {
  if (state === 'running') {
    return (
      <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
        <circle cx="4" cy="4" r="3" fill="none" stroke="currentColor" strokeWidth="1.4" />
        <path d="M4 1.4A2.6 2.6 0 0 1 6.6 4" stroke="currentColor" strokeWidth="1.4" fill="none" />
      </svg>
    )
  }
  if (state === 'failed') {
    return (
      <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
        <path d="M1.6 1.6l4.8 4.8M6.4 1.6L1.6 6.4" stroke="currentColor" strokeWidth="1.4" />
      </svg>
    )
  }
  if (state === 'passed') {
    return (
      <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
        <path
          d="M1.4 4.2 3.2 6l3.4-4"
          stroke="currentColor"
          strokeWidth="1.4"
          fill="none"
          strokeLinecap="round"
        />
      </svg>
    )
  }
  if (state === 'awaiting-approval') {
    return (
      <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
        <path d="M4 0.8 7.2 6.6H0.8z" fill="none" stroke="currentColor" strokeWidth="1.1" />
      </svg>
    )
  }
  return (
    <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
      <circle cx="4" cy="4" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  )
}

export function KeyValue({ label, value }: { label: string; value: ReactNode }): JSX.Element {
  return (
    <div className="flex items-baseline justify-between gap-3 py-[3px]">
      <span className="shrink-0 text-tiny text-fg-mute">{label}</span>
      <span className="min-w-0 truncate text-tiny text-fg-dim">{value}</span>
    </div>
  )
}

/** Compact usage meter. Bar plus number — the number is the source of truth. */
export function Meter({
  label,
  value,
  max,
  display,
  tone = 'accent'
}: {
  label: string
  value: number
  max: number
  display: string
  tone?: 'accent' | 'warn'
}): JSX.Element {
  const pct = Math.min(100, Math.round((value / max) * 100))
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-micro text-fg-mute">{label}</span>
        <span className="font-mono text-tiny text-fg-dim">{display}</span>
      </div>
      <div className="mt-1 h-[3px] w-full bg-active">
        <div
          className={`h-full ${tone === 'warn' ? 'bg-warn' : 'bg-accent'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

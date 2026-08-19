import { useEffect, useState, type JSX } from 'react'
import { CAUSE_LABEL } from '@domains/pipeline/death'
import type { DeathCause, SliceFailure } from '@domains/pipeline/types'

/**
 * A4 — 죽음 표시. What killed this slice, and what it had done when it died.
 *
 * The cause is the engine's own (`recovery.py` classify, over the engine's own
 * quota flag and telemetry subtype), and the evidence line says which reading
 * produced it — a guess has to read as a guess. Everything else on this panel
 * is a file quoted as it is: `state.reason`, the run's last words, and
 * `progress.md`.
 *
 * @param failure  the report main assembled, or null while it loads
 */
export function FailurePanel({ failure }: { failure: SliceFailure | null }): JSX.Element | null {
  const left = useCountdown(failure?.quotaWait?.resumeAt ?? null, Boolean(failure?.quotaWait))

  if (!failure) return null

  const wait = failure.quotaWait

  return (
    <div className="flex flex-col gap-2 border-t border-line px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <CauseBadge cause={failure.cause} />
        {failure.stage ? (
          <span className="font-mono text-micro text-fg-dim">
            stage <span className="text-fg">{failure.stage}</span>
          </span>
        ) : null}
        {failure.runId ? (
          <span className="font-mono text-micro text-fg-mute" title="run id">
            {failure.runId}
          </span>
        ) : null}
        {failure.exitCode !== null ? (
          <span className="font-mono text-micro text-fg-mute">exit {failure.exitCode}</span>
        ) : null}
      </div>

      <p className="text-micro text-fg-mute">{failure.causeEvidence}</p>

      {wait ? (
        <p className="font-mono text-tiny text-warn">
          {left > 0
            ? `한도 대기 — ${formatLeft(left)} left (resumes ${short(wait.resumeAt)})`
            : `the wait is over — resume it${wait.resumeAt ? ` (was ${short(wait.resumeAt)})` : ''}`}
          {wait.retries > 0 ? ` · retry ${wait.retries}` : ''}
          {wait.source ? ` · ${wait.source}` : ''}
        </p>
      ) : null}

      {failure.stopped ? (
        <p className="text-tiny text-warn">
          자동 복구 중지 — <span className="font-mono">{failure.stopped.pin}</span>:{' '}
          {failure.stopped.detail}
        </p>
      ) : null}

      {failure.reason ? (
        <p className="text-tiny break-words text-fg-dim">{failure.reason}</p>
      ) : null}

      {failure.summary ? (
        <Block label="last words of the run">{failure.summary}</Block>
      ) : null}

      {failure.progress.exists ? (
        <Block label="progress.md">{failure.progress.text}</Block>
      ) : (
        <p className="font-mono text-micro text-fg-mute" title={failure.progress.path}>
          no progress.md — the stage left no note
        </p>
      )}
    </div>
  )
}

/**
 * The cause, in a badge whose shape (not only colour) carries the meaning.
 *
 * @param cause  what `classifyCause` answered
 */
function CauseBadge({ cause }: { cause: DeathCause }): JSX.Element {
  const tone =
    cause === 'usage_limit'
      ? 'border-warn/40 bg-warn/10 text-warn'
      : cause === 'turns'
        ? 'border-accent/40 bg-accent-soft text-accent'
        : 'border-bad/40 bg-bad/10 text-bad'
  return (
    <span className={`shrink-0 rounded-sm border px-1.5 py-px font-mono text-micro ${tone}`}>
      {CAUSE_LABEL[cause]}
    </span>
  )
}

/**
 * One quoted file, scrollable, in a fixed frame so a long note cannot push the
 * buttons off screen.
 *
 * @param label     what is being quoted
 * @param children  the text, as it is on disk
 */
function Block({ label, children }: { label: string; children: string }): JSX.Element {
  return (
    <div className="min-w-0">
      <p className="panel-label">{label}</p>
      <pre className="mt-0.5 max-h-40 overflow-auto rounded-sm border border-line bg-app px-2 py-1 font-mono text-micro leading-relaxed break-words whitespace-pre-wrap text-fg-dim select-text">
        {children}
      </pre>
    </div>
  )
}

/**
 * Seconds left until an ISO timestamp, ticking once a second.
 *
 * The countdown is computed here rather than fetched, so it moves without
 * re-reading three files every second.
 *
 * @param resumeAt  when the wait ends, or null
 * @param active    is there a wait at all?
 * @flow  no wait -> 0 and no timer ; else recompute every second
 */
function useCountdown(resumeAt: string | null, active: boolean): number {
  const [left, setLeft] = useState(0)

  useEffect(() => {
    if (!resumeAt || !active) {
      setLeft(0)
      return
    }
    const at = Date.parse(resumeAt)
    if (!Number.isFinite(at)) {
      setLeft(0)
      return
    }
    const tick = (): void => setLeft(Math.max(0, Math.round((at - Date.now()) / 1000)))
    tick()
    const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
  }, [resumeAt, active])

  return left
}

/**
 * `1h 04m` / `9m 12s` / `41s` — the shape a waiting human reads at a glance.
 *
 * @param seconds  how many are left
 * @flow  hours when there are any, then minutes, then bare seconds
 */
function formatLeft(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`
  return `${s}s`
}

/**
 * The clock part of an ISO timestamp, or the timestamp when it is not one.
 *
 * @param iso  a timestamp from state.json, or null
 */
function short(iso: string | null): string {
  if (!iso) return ''
  const at = Date.parse(iso)
  if (!Number.isFinite(at)) return iso
  const d = new Date(at)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

import type { JSX } from 'react'

/**
 * A slice status straight from `state.json`, coloured but never *only*
 * coloured — the mark changes shape too, so the badge survives greyscale.
 *
 * The status set is open (`running:<stage>`, and whatever v0.5 adds), so this
 * classifies by prefix and falls back to muted rather than dropping a status
 * it has not seen before.
 */
type Tone = 'wait' | 'run' | 'ok' | 'bad' | 'mute'

const TONE_CLASS: Record<Tone, string> = {
  wait: 'text-accent',
  run: 'text-warn',
  ok: 'text-ok',
  bad: 'text-bad',
  mute: 'text-fg-mute'
}

function toneFor(status: string): Tone {
  if (status.startsWith('waiting_approval')) return 'wait'
  if (status.startsWith('running') || status === 'quota_wait') return 'run'
  if (status === 'done' || status === 'merged') return 'ok'
  if (status === 'failed' || status === 'rejected' || status === '(unreadable)') return 'bad'
  return 'mute'
}

export function SliceStatusBadge({ status }: { status: string }): JSX.Element {
  const tone = toneFor(status)
  return (
    <span
      className={`flex shrink-0 items-center gap-1 font-mono text-micro font-semibold ${TONE_CLASS[tone]}`}
      title={status}
    >
      <Mark tone={tone} />
      {status}
    </span>
  )
}

function Mark({ tone }: { tone: Tone }): JSX.Element {
  if (tone === 'wait') {
    // The same triangle the mock shell uses for "needs approval": one visual
    // language for one meaning.
    return (
      <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
        <path d="M4 0.8 7.2 6.6H0.8z" fill="none" stroke="currentColor" strokeWidth="1.1" />
      </svg>
    )
  }
  if (tone === 'run') {
    return (
      <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
        <circle cx="4" cy="4" r="3" fill="none" stroke="currentColor" strokeWidth="1.4" />
        <path d="M4 1.4A2.6 2.6 0 0 1 6.6 4" stroke="currentColor" strokeWidth="1.4" fill="none" />
      </svg>
    )
  }
  if (tone === 'ok') {
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
  if (tone === 'bad') {
    return (
      <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
        <path d="M1.6 1.6l4.8 4.8M6.4 1.6L1.6 6.4" stroke="currentColor" strokeWidth="1.4" />
      </svg>
    )
  }
  return (
    <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
      <circle cx="4" cy="4" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  )
}

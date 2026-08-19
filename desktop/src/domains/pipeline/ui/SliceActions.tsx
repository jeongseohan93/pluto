import { useEffect, useState, type JSX } from 'react'
import type { SliceState } from '@shared/ide'
import { discardGuard } from '@domains/pipeline/guards'
import { MAX_STAGE_TURNS } from '@domains/pipeline/commands'
import type { CommandRequest, CommandResult } from '@domains/pipeline/types'

/** Statuses that are over: there is nothing left to resume. */
const FINISHED = ['done', 'merged', 'discarded']

/**
 * A3 — 재개·마감. Resume a stopped slice, land a finished one, throw one away.
 *
 * Three buttons, three existing commands. The only judgement in this file is
 * the ★ guard: `discard_slice` never asks whether the slice was merged, and
 * `--merge` refuses in ways that leave nothing on disk, so the check that stops
 * the 8/18 accident can only live here. See `guards.ts` — this component just
 * draws its answer.
 *
 * @param slice      the selected slice
 * @param busy       a command is already running (one slot)
 * @param lastMerge  the last merge this session ran, for the guard
 * @param onRun      hand a request to the command slot
 * @flow  resume while the slice is not finished -> merge only when it is done
 *        -> discard behind the guard, two-step when it has never been merged
 */
export function SliceActions({
  slice,
  busy,
  lastMerge,
  onRun
}: {
  slice: SliceState
  busy: boolean
  lastMerge: CommandResult | null
  onRun: (request: CommandRequest) => void
}): JSX.Element {
  const [turns, setTurns] = useState('')
  const [push, setPush] = useState(true)
  const [confirming, setConfirming] = useState(false)

  // A confirmation belongs to one slice; carrying it across would arm the
  // second click on a slice the human never armed.
  useEffect(() => {
    setConfirming(false)
    setTurns('')
  }, [slice.id])

  const guard = discardGuard(slice, lastMerge)
  const resumable = !FINISHED.includes(slice.status) && slice.status !== '(unreadable)'
  const mergeable = slice.status === 'done'
  const parsedTurns = Number.parseInt(turns, 10)
  const turnsOk =
    turns.trim() === '' ||
    (Number.isInteger(parsedTurns) && parsedTurns >= 1 && parsedTurns <= MAX_STAGE_TURNS)

  function resume(): void {
    const request: CommandRequest =
      turns.trim() === ''
        ? { kind: 'resume', sliceId: slice.id }
        : { kind: 'resume', sliceId: slice.id, implementTurns: parsedTurns }
    onRun(request)
  }

  function discard(): void {
    if (guard.confirm && !confirming) {
      setConfirming(true)
      return
    }
    setConfirming(false)
    onRun({ kind: 'discard', sliceId: slice.id })
  }

  return (
    <div className="flex flex-col gap-1.5 border-t border-line px-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {resumable ? (
          <>
            <button
              type="button"
              disabled={busy || !turnsOk}
              onClick={resume}
              title={`aidev pipeline --resume-slice ${slice.id}`}
              className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-tiny text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
            >
              Resume
            </button>
            <input
              value={turns}
              onChange={(event) => setTurns(event.target.value.replace(/[^0-9]/g, ''))}
              placeholder="turns"
              inputMode="numeric"
              title={`optional: --max-turns-stage implement=N (1..${MAX_STAGE_TURNS})`}
              className={`w-16 shrink-0 rounded-sm border bg-app px-1.5 py-0.5 text-center font-mono text-micro text-fg placeholder:text-fg-mute ${
                turnsOk ? 'border-line' : 'border-bad'
              }`}
            />
          </>
        ) : null}

        {mergeable ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => onRun({ kind: 'merge', sliceId: slice.id, push })}
              title={`aidev pipeline --merge ${slice.id}${push ? ' --push' : ''}`}
              className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-tiny text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
            >
              {push ? 'Merge + push' : 'Merge'}
            </button>
            <label className="flex shrink-0 items-center gap-1 text-micro text-fg-mute">
              <input
                type="checkbox"
                checked={push}
                disabled={busy}
                onChange={(event) => setPush(event.target.checked)}
              />
              push
            </label>
          </>
        ) : null}

        {slice.status === 'discarded' ? null : (
          <button
            type="button"
            disabled={busy || !guard.allowed}
            onClick={discard}
            title={guard.allowed ? `aidev pipeline --discard ${slice.id}` : guard.reason}
            className={`shrink-0 rounded-sm border px-2 py-0.5 text-tiny disabled:opacity-40 ${
              confirming
                ? 'border-bad bg-bad/10 text-bad'
                : 'border-line text-fg-dim hover:bg-hover hover:text-bad'
            }`}
          >
            {confirming ? 'Discard — really?' : 'Discard'}
          </button>
        )}
        {confirming ? (
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-tiny text-fg-mute hover:bg-hover hover:text-fg-dim"
          >
            Cancel
          </button>
        ) : null}
      </div>

      <p
        className={`text-micro ${
          guard.tone === 'block' ? 'text-bad' : guard.tone === 'warn' ? 'text-warn' : 'text-fg-mute'
        }`}
      >
        {guard.tone === 'block' ? '⚠ ' : ''}
        {guard.reason}
      </p>
      {!turnsOk ? (
        <p className="text-micro text-bad">turns must be a whole number 1..{MAX_STAGE_TURNS}</p>
      ) : null}
    </div>
  )
}

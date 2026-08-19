import { useEffect, useState, type JSX } from 'react'
import type { ApprovalDecision, ApprovalResult, SliceState, StageArtifact } from '@shared/ide'
import { EmptyState } from '@renderer/components/primitives'
import { SliceActions } from '@domains/pipeline/ui/SliceActions'
import { FailurePanel } from '@domains/pipeline/ui/FailurePanel'
import { isDeadStatus } from '@domains/pipeline/ui/useSliceFailure'
import type { CommandRequest, CommandResult, SliceFailure } from '@domains/pipeline/types'

export interface PlanSurfaceProps {
  slice: SliceState | null
  artifact: StageArtifact | null
  onDecide: (decision: ApprovalDecision, reason: string) => Promise<ApprovalResult>
  /** A command is running — one slot, so the finishing buttons are disabled. */
  busy: boolean
  /** The last merge this session ran, for the discard guard. */
  lastMerge: CommandResult | null
  /** Why the slice died, when it did. */
  failure: SliceFailure | null
  onRun: (request: CommandRequest) => void
}

/**
 * The stage output, the one decision the human makes about it, and — since
 * v0.2.6 — the buttons that finish the slice off.
 *
 * Not rendered as markdown on purpose: this is the text the pipeline will act
 * on, and showing it as it is on disk is the only reading that cannot mislead.
 *
 * A2: one comment box, shared by Approve and Reject. `approved: <문구>` is the
 * conditional approval `read_decision` already understands — the sentence after
 * the colon survives as the decision's reason and is carried into every later
 * stage. So the same box means "conditions" on the left and "why not" on the
 * right, and the result line echoes the condition back so a human can see that
 * it landed.
 *
 * @param slice      the selected slice
 * @param artifact   the file behind the open gate
 * @param onDecide   write the approval
 * @param busy       is a command running?
 * @param lastMerge  the last merge this session ran
 * @param failure    why it died, when it did
 * @param onRun      start resume / merge / discard
 * @flow  no slice -> an empty state ; a gate open -> the decision row ; a dead
 *        slice -> the failure panel ; always the finishing buttons
 */
export function PlanSurface({
  slice,
  artifact,
  onDecide,
  busy,
  lastMerge,
  failure,
  onRun
}: PlanSurfaceProps): JSX.Element {
  const [reason, setReason] = useState('')
  const [working, setWorking] = useState(false)
  const [result, setResult] = useState<ApprovalResult | null>(null)

  // A decision belongs to one slice. Carrying it across would let a message
  // about the last approval sit under the next slice's plan.
  useEffect(() => {
    setReason('')
    setResult(null)
  }, [slice?.id, slice?.waitingStage])

  if (!slice) {
    return (
      <EmptyState
        title="No slice selected"
        hint="Pick a slice in the Pipeline panel to read what it is waiting on."
      />
    )
  }

  const stage = slice.waitingStage

  async function decide(decision: ApprovalDecision): Promise<void> {
    setWorking(true)
    try {
      setResult(await onDecide(decision, reason))
    } finally {
      setWorking(false)
    }
  }

  return (
    <div className="flex h-full min-w-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-1.5">
        <span className="min-w-0 flex-1 truncate text-tiny text-fg-dim" title={slice.id}>
          {slice.id}
        </span>
        <span
          className="min-w-0 shrink truncate font-mono text-micro text-fg-mute"
          title={artifact?.path ?? ''}
        >
          {artifact?.path ?? ''}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        {!artifact ? (
          <p className="text-tiny text-fg-mute">Loading…</p>
        ) : artifact.exists ? (
          <pre className="select-text font-mono text-tiny leading-relaxed break-words whitespace-pre-wrap text-fg-dim">
            {artifact.text}
          </pre>
        ) : (
          <p className="text-tiny text-fg-mute">
            Nothing written yet at {artifact.path}. The stage has not produced it.
          </p>
        )}
      </div>

      <div className="max-h-[55%] shrink-0 overflow-y-auto">
        <div className="border-t border-line px-3 py-2">
          {stage ? (
            <div className="flex items-center gap-2">
              <span className="shrink-0 text-tiny text-fg-dim">
                Gate <span className="font-mono text-accent">{stage}</span>
              </span>
              <button
                type="button"
                disabled={working}
                onClick={() => decide('approved')}
                title={
                  reason.trim() === '' ? 'writes: approved' : `writes: approved: ${reason.trim()}`
                }
                className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-tiny text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
              >
                Approve
              </button>
              <input
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="conditions to approve with — or the reason to reject"
                className="min-w-0 flex-1 rounded-sm border border-line bg-app px-2 py-0.5 text-tiny text-fg placeholder:text-fg-mute"
              />
              <button
                type="button"
                // The convention allows an empty reason, but a rejection with no
                // reason lands in state.json as "(no reason given)" — so the UI
                // asks for one rather than recording that.
                disabled={working || reason.trim() === ''}
                onClick={() => decide('rejected')}
                className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-tiny text-fg-dim hover:bg-hover hover:text-bad disabled:opacity-40"
              >
                Reject
              </button>
            </div>
          ) : (
            <p className="text-tiny text-fg-mute">No gate open — this slice is {slice.status}.</p>
          )}

          {result ? <ResultLine result={result} comment={reason} /> : null}
        </div>

        <SliceActions slice={slice} busy={busy} lastMerge={lastMerge} onRun={onRun} />
        {isDeadStatus(slice.status) ? <FailurePanel failure={failure} /> : null}
      </div>
    </div>
  )
}

/**
 * What the pipeline will read back, said plainly — including the condition, so
 * a conditional approval can be seen to have been recorded as one.
 *
 * @param result   what `writeApproval` reported after re-reading the file
 * @param comment  the line the human typed
 * @flow  written -> the verdict and any condition ; else the specific refusal
 */
function ResultLine({ result, comment }: { result: ApprovalResult; comment: string }): JSX.Element {
  if (result.ok) {
    const condition = comment.trim()
    return (
      <p className="mt-1.5 font-mono text-micro text-ok" title={result.path}>
        {result.effective}
        {result.effective === 'approved' && condition ? `: ${condition}` : ''} — {result.path}
      </p>
    )
  }
  const message = result.conflict
    ? `already decided as "${result.conflict}" — nothing was written`
    : result.effective === 'pending'
      ? 'the first line of the file does not read as a decision — open it and check'
      : (result.error ?? 'the decision could not be written')
  return (
    <p className="mt-1.5 font-mono text-micro text-bad" title={result.path}>
      {message}
      {result.path ? ` — ${result.path}` : ''}
    </p>
  )
}

import { useEffect, useState, type JSX } from 'react'
import type { ApprovalDecision, ApprovalResult, SliceState, StageArtifact } from '@shared/ide'
import { EmptyState } from '@renderer/components/primitives'

export interface PlanSurfaceProps {
  slice: SliceState | null
  artifact: StageArtifact | null
  onDecide: (decision: ApprovalDecision, reason: string) => Promise<ApprovalResult>
}

/**
 * The stage output, and the one decision the human makes about it.
 *
 * Not rendered as markdown on purpose: this is the text the pipeline will act
 * on, and showing it as it is on disk is the only reading that cannot mislead.
 */
export function PlanSurface({ slice, artifact, onDecide }: PlanSurfaceProps): JSX.Element {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
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
    setBusy(true)
    try {
      setResult(await onDecide(decision, reason))
    } finally {
      setBusy(false)
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

      <div className="shrink-0 border-t border-line px-3 py-2">
        {stage ? (
          <div className="flex items-center gap-2">
            <span className="shrink-0 text-tiny text-fg-dim">
              Gate <span className="font-mono text-accent">{stage}</span>
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={() => decide('approved')}
              className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-tiny text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
            >
              Approve
            </button>
            <input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="reason for rejection"
              className="min-w-0 flex-1 rounded-sm border border-line bg-app px-2 py-0.5 text-tiny text-fg placeholder:text-fg-mute"
            />
            <button
              type="button"
              // The convention allows an empty reason, but a rejection with no
              // reason lands in state.json as "(no reason given)" — so the UI
              // asks for one rather than recording that.
              disabled={busy || reason.trim() === ''}
              onClick={() => decide('rejected')}
              className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-tiny text-fg-dim hover:bg-hover hover:text-bad disabled:opacity-40"
            >
              Reject
            </button>
          </div>
        ) : (
          <p className="text-tiny text-fg-mute">
            No gate open — this slice is {slice.status}.
          </p>
        )}

        {result ? <ResultLine result={result} /> : null}
      </div>
    </div>
  )
}

function ResultLine({ result }: { result: ApprovalResult }): JSX.Element {
  if (result.ok) {
    return (
      <p className="mt-1.5 font-mono text-micro text-ok" title={result.path}>
        {result.effective} — {result.path}
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

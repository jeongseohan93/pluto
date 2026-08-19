/**
 * ★ The 2026-08-18 gen2b accident, in code.
 *
 * `discard_slice` (pipeline.py) never looks at whether the slice was merged —
 * it removes the worktree and the branch on request, full stop. And `--merge`
 * refuses in three ways that leave *no trace on disk at all* (the checkout is
 * not on base, the checkout is dirty, the worktree is dirty): only a real merge
 * conflict writes `state["merge"] = {"status": "conflict"}`.
 *
 * So a UI that decided from `state.json` alone would be blind to the exact
 * failure that lost work that night. This guard therefore reads two things: the
 * durable record, and what the app itself just watched happen. Both are needed;
 * neither is sufficient.
 *
 * Pure on purpose — the button and the test read the same function.
 */
import type { SliceState } from '../../shared/ide'
import type { CommandResult } from './types'

export type GuardTone = 'ok' | 'warn' | 'block'

export interface DiscardGuard {
  /** May the button be pressed at all? */
  allowed: boolean
  /** Allowed, but ask a second time first. */
  confirm: boolean
  tone: GuardTone
  /** Shown next to the button, in the words of what actually happened. */
  reason: string
}

/**
 * What the Discard button is allowed to do for this slice, and what to say.
 *
 * Refusing is reserved for the case where discarding would destroy commits
 * that were never merged. "Never merged at all" is *not* that case — a slice
 * you decided against is exactly the thing discard is for — so it asks twice
 * instead of forbidding.
 *
 * @param slice      the slice as `state.json` last published it
 * @param lastMerge  the last merge this app ran, if it was this slice's
 * @flow  a recorded conflict -> block ; recorded as merged -> allow (warning
 *        when only the push failed) ; a merge we watched fail with nothing on
 *        disk -> block ; no merge record at all -> allow, but two-step
 */
export function discardGuard(
  slice: SliceState | null,
  lastMerge: CommandResult | null
): DiscardGuard {
  if (!slice) {
    return { allowed: false, confirm: false, tone: 'block', reason: 'no slice selected' }
  }

  const merge = slice.merge

  // 1. A conflict is one of the two refusals the engine records. It stops; it
  //    never resolves. The branch still holds the only copy of the work.
  if (merge && merge.status === 'conflict') {
    const files = merge.conflicts.slice(0, 5).join(', ')
    return {
      allowed: false,
      confirm: false,
      tone: 'block',
      reason: `the merge stopped on a conflict${files ? ` (${files})` : ''} — resolve it and merge before discarding`
    }
  }

  // 2. Merged. The commits are on the base branch, so the branch is expendable.
  //    Checked before the session's own memory because `--merge --push` exits
  //    non-zero when only the push fails, and the merge itself still stands.
  if (merge && merge.status === 'merged') {
    if (merge.push && merge.push.status !== 'pushed') {
      return {
        allowed: true,
        confirm: false,
        tone: 'warn',
        reason: `merged, but the push failed${merge.push.error ? ` (${merge.push.error})` : ''} — the merge stands, only the backup did not happen`
      }
    }
    return {
      allowed: true,
      confirm: false,
      tone: 'ok',
      reason: `merged into ${merge.base ?? 'its base'}${merge.commit ? ` (${merge.commit.slice(0, 7)})` : ''}`
    }
  }

  // 3. This session watched a merge of *this* slice fail and disk says nothing.
  //    That is the accident's exact shape, and this is its only witness.
  if (
    lastMerge &&
    lastMerge.kind === 'merge' &&
    lastMerge.sliceId === slice.id &&
    !mergeSucceeded(lastMerge)
  ) {
    return {
      allowed: false,
      confirm: false,
      tone: 'block',
      reason: `merge failed (${failureLabel(lastMerge)}) — discarding now would delete commits that were never merged. Fix what the Run log says, merge again, then discard.`
    }
  }

  // 4. No merge record. Legitimate for a slice being thrown away — but it is
  //    also what a closed-and-reopened app looks like after case 3, so it asks.
  return {
    allowed: true,
    confirm: true,
    tone: 'warn',
    reason: 'this slice has never been merged — its branch and commits will be deleted'
  }
}

/**
 * Did a merge command actually land? `--push` failure exits non-zero too, but
 * that case leaves `status: "merged"` behind and is handled from state.
 *
 * @param result  the finished merge command
 */
function mergeSucceeded(result: CommandResult): boolean {
  return result.error === null && result.exitCode === 0
}

/**
 * How to name a failed merge in one parenthesis.
 *
 * @param result  the finished merge command
 */
function failureLabel(result: CommandResult): string {
  if (result.error) return result.error
  return result.exitCode === null ? 'stopped' : `exit ${result.exitCode}`
}

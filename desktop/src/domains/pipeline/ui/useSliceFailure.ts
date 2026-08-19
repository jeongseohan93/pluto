/**
 * Why the selected slice died — fetched only when there is a death to explain.
 *
 * Reading it costs a state.json, a runs.json and a telemetry.json, so it is not
 * folded into the 2s repo poll: a healthy slice must not pay for a panel it
 * never shows.
 */
import { useEffect, useState } from 'react'
import type { SliceFailure } from '@domains/pipeline/types'

/** Statuses that have something to explain. */
const DEAD = ['failed', 'quota_wait', 'rejected']

/**
 * Is this a status the failure panel should open for?
 *
 * @param status  the slice's `state.json` status
 */
export function isDeadStatus(status: string | null | undefined): boolean {
  return typeof status === 'string' && DEAD.includes(status)
}

/**
 * The failure report for this slice, or null when there is nothing to report.
 *
 * @param sliceId  the selected slice, or null
 * @param status   its status — the panel opens for failed / quota_wait / rejected
 * @param nonce    bump to re-read after a resume finishes
 * @flow  not a dead slice -> clear ; else fetch, and drop the answer if the
 *        selection moved while it was in flight
 */
export function useSliceFailure(
  sliceId: string | null,
  status: string | null,
  nonce = 0
): SliceFailure | null {
  const [loaded, setLoaded] = useState<{ key: string; data: SliceFailure | null } | null>(null)
  const key = `${sliceId ?? ''} ${status ?? ''} ${nonce}`

  useEffect(() => {
    if (!sliceId || !isDeadStatus(status)) {
      setLoaded(null)
      return
    }
    let alive = true
    window.aidev.getSliceFailure(sliceId).then((data) => {
      if (alive) setLoaded({ key, data })
    })
    return () => {
      alive = false
    }
    // `key` carries every input; listing them again would only re-run this
    // effect for the same fetch.
  }, [key, sliceId, status])

  return loaded?.key === key ? loaded.data : null
}

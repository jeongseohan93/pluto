/**
 * The three parts of `state.json` this slice started reading, as pure parsers.
 *
 * `merge`, `quota` and `recovery.stopped` are written by `pipeline.py` and read
 * by two different callers here — the slice list and the failure panel — so the
 * parsing lives in one place that touches no filesystem and can be tested with
 * a literal.
 *
 * Tolerant in the same sense as everything else that reads these files: the
 * pipeline is writing them while we read, so every field is optional and an
 * unexpected shape is `null`, never a throw.
 */
import type { SliceMerge, SliceMergePush, SliceQuota, SliceStopped } from './types'

/**
 * `state["merge"]` — what `merge_slice` recorded, or null if it never ran.
 *
 * @param state  a parsed state.json, or null
 * @flow  not a record -> null ; else status/branch/base/commit/conflicts, and
 *        the push sub-record when `--push` was asked for
 */
export function readMerge(state: Record<string, unknown> | null): SliceMerge | null {
  const merge = record(state?.merge)
  if (!merge) return null
  return {
    status: str(merge.status) ?? '',
    at: str(merge.at) ?? null,
    branch: str(merge.branch) ?? null,
    base: str(merge.base) ?? null,
    commit: str(merge.commit) ?? null,
    conflicts: strList(merge.conflicts),
    push: readPush(merge.push)
  }
}

/**
 * `state["merge"]["push"]` — the backup, which fails on its own terms.
 *
 * @param value  the `push` field of a merge record
 */
function readPush(value: unknown): SliceMergePush | null {
  const push = record(value)
  if (!push) return null
  return {
    status: str(push.status) ?? '',
    remote: str(push.remote) ?? null,
    error: str(push.error) ?? null
  }
}

/**
 * `state["quota"]` — the usage-limit wait currently open, if there is one.
 *
 * @param state  a parsed state.json, or null
 */
export function readQuota(state: Record<string, unknown> | null): SliceQuota | null {
  const quota = record(state?.quota)
  if (!quota) return null
  return {
    waiting: quota.waiting === true,
    resumeAt: str(quota.resume_at) ?? null,
    retries: num(quota.retries) ?? 0,
    source: str(quota.source) ?? null
  }
}

/**
 * `state["recovery"]["stopped"]` — which safety pin ended the automation.
 *
 * @param state  a parsed state.json, or null
 */
export function readStopped(state: Record<string, unknown> | null): SliceStopped | null {
  const stopped = record(record(state?.recovery)?.stopped)
  if (!stopped) return null
  return {
    at: str(stopped.at) ?? null,
    stage: str(stopped.stage) ?? null,
    pin: str(stopped.pin) ?? '',
    detail: str(stopped.detail) ?? ''
  }
}

/**
 * The stage that failed: the last one in declared order whose status says so.
 *
 * Declared order rather than the dict's order, because `stage_order` is what
 * the pipeline itself walks — the stage that died is the furthest it reached.
 *
 * @param state  a parsed state.json, or null
 * @flow  walk stage_order (then any extra key) -> remember every failed one ->
 *        the last is the answer ; none failed -> null
 */
export function failedStage(state: Record<string, unknown> | null): string | null {
  const stages = record(state?.stages)
  if (!stages) return null
  const order: string[] = []
  for (const name of strList(state?.stage_order)) if (!order.includes(name)) order.push(name)
  for (const name of Object.keys(stages)) if (!order.includes(name)) order.push(name)

  let last: string | null = null
  for (const name of order) {
    const entry = record(stages[name])
    if (entry && str(entry.status) === 'failed') last = name
  }
  return last
}

/**
 * How many seconds are left of a wait that ends at this ISO timestamp.
 *
 * Never negative: a wait whose end has passed is over, and showing "-4m" would
 * only make a reader wonder whether the clock is wrong.
 *
 * @param resumeAt  the ISO timestamp `record_wait` stored, or null
 * @param now       the current epoch milliseconds
 */
export function secondsLeft(resumeAt: string | null, now: number): number {
  if (!resumeAt) return 0
  const at = Date.parse(resumeAt)
  if (!Number.isFinite(at)) return 0
  return Math.max(0, Math.round((at - now) / 1000))
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

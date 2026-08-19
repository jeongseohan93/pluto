/**
 * Why a stage died, in the same three kinds the engine uses.
 *
 * A literal port of `aidev/recovery.py` `is_turn_death` / `classify`, and
 * nothing more. The app must not invent a fourth kind or a different threshold:
 * the point of showing a cause is that it is *the engine's* cause, the one that
 * decided whether auto-recovery bought another session.
 *
 * `quota` is passed in, never re-derived. `pipeline.looks_like_quota` owns that
 * marker list and the engine already wrote its answer into `runs.json` as
 * `quota: true` — a second copy here is how the two readings would drift apart.
 */
import type { DeathCause } from './types'

export const CAUSE_QUOTA: DeathCause = 'usage_limit'
export const CAUSE_TURNS: DeathCause = 'turns'
export const CAUSE_OTHER: DeathCause = 'other'

/** `recovery.py` TURN_MARKERS, in the same order. */
export const TURN_MARKERS = ['error_max_turns', 'max_turns', 'maximum number of turns'] as const

/** The exact subtypes `claude -p` reports when it hits `--max-turns`. */
const EXACT_SUBTYPES = ['error_max_turns', 'max_turns']

/**
 * Did this stage die at its turn limit, as either the CLI or the log tells it?
 *
 * Both sources are read, exactly as the Python does: telemetry is derived from
 * the same stream the text came from, so agreeing is cheap and disagreeing is
 * what keeps one misparse from being the whole answer.
 *
 * @param subtype  `exact.result_subtype` from the run's telemetry, if any
 * @param text     the tail of the run — the result event and stderr
 * @flow  the exact marker -> yes ; else any marker anywhere in the text
 */
export function isTurnDeath(subtype: string | null | undefined, text: string): boolean {
  const exact = String(subtype ?? '')
    .trim()
    .toLowerCase()
  if (EXACT_SUBTYPES.includes(exact)) return true
  const lowered = String(text ?? '').toLowerCase()
  return TURN_MARKERS.some((marker) => lowered.includes(marker))
}

/**
 * The cause of death: a limit wins over everything, then a turn death.
 *
 * @param subtype  `exact.result_subtype` from the run's telemetry, if any
 * @param text     the tail of the run — the result event and stderr
 * @param quota    what the engine already decided about the same text
 * @flow  quota -> usage_limit ; turn death -> turns ; anything else -> other
 */
export function classifyCause(
  subtype: string | null | undefined,
  text: string,
  quota: boolean
): DeathCause {
  if (quota) return CAUSE_QUOTA
  if (isTurnDeath(subtype, text)) return CAUSE_TURNS
  return CAUSE_OTHER
}

/**
 * One line saying which reading produced the cause, so a guess reads as one.
 *
 * @param cause    what `classifyCause` answered
 * @param subtype  `exact.result_subtype`, if the run's telemetry was readable
 * @param quota    the engine's own quota flag from runs.json / state.json
 * @flow  one sentence per cause, naming the evidence that decided it
 */
export function causeEvidence(
  cause: DeathCause,
  subtype: string | null | undefined,
  quota: boolean
): string {
  if (cause === CAUSE_QUOTA) {
    return quota
      ? 'the engine marked this run as a usage limit (runs.json quota)'
      : 'state.json says the slice is waiting out a usage limit'
  }
  if (cause === CAUSE_TURNS) {
    return subtype
      ? `telemetry ended with result_subtype "${subtype}"`
      : 'the run log ends on a turn-limit marker'
  }
  return 'neither a usage limit nor a turn limit — read the reason and progress below'
}

/** What to call each cause on screen. */
export const CAUSE_LABEL: Record<DeathCause, string> = {
  usage_limit: 'USAGE LIMIT',
  turns: 'OUT OF TURNS',
  other: 'FAILED'
}

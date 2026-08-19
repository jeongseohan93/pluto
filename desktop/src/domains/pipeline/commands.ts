/**
 * Every button in the app, as the command line it already is.
 *
 * This module is the single place a `CommandRequest` becomes argv, and it is
 * pure: no spawn, no fs, no Electron. That is what makes the wiring checkable —
 * `commands.test.ts` can assert the exact five command lines the requirement
 * names without running anything.
 *
 * The renderer never sends a command line. It sends a slice id, a `tasks/*.md`
 * path, or a turn count, and every one of them has to survive the guards below
 * before it reaches a process. `shell: false` does the rest.
 */
import type { CommandKind, CommandRequest, CommandResult, CommandState } from './types'

/** `aidev/recovery.py` DEFAULT_TURN_CAP is 300; this is only a sanity bound. */
export const MAX_STAGE_TURNS = 1000

/** Same rule as `aidev-store.ts` `safeSegment`: one directory name, no reach. */
const SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

/** `tasks/<name>.md` and nothing else — the only shape the launcher offers. */
const REQUIREMENT = /^tasks\/[A-Za-z0-9][A-Za-z0-9._-]*\.md$/

/**
 * Is this one path segment, with no separator and no way out of its directory?
 *
 * @param value  the candidate, from the renderer
 */
export function isSafeSegment(value: string): boolean {
  return typeof value === 'string' && SEGMENT.test(value) && !value.includes('..')
}

/**
 * Is this a repo-relative requirement path the launcher could have offered?
 *
 * @param value  the candidate, from the renderer
 */
export function isRequirementPath(value: string): boolean {
  return typeof value === 'string' && REQUIREMENT.test(value) && !value.includes('..')
}

/**
 * Is this a turn budget a human could have meant: a whole number, 1..1000?
 *
 * @param value  the candidate, from the renderer
 */
export function isStageTurns(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    value >= 1 &&
    value <= MAX_STAGE_TURNS
  )
}

/**
 * The argv for one request — everything after the `aidev` binary itself.
 *
 * Throws rather than returning a fallback: a request that did not pass the
 * guards is a bug or an attack, and neither should be answered with a
 * *slightly different* command running against a real repository.
 *
 * @param repoRoot  the repository the command runs against (also its cwd)
 * @param request   what the renderer asked for
 * @flow  reject an unusable repo -> one branch per kind, each validating its
 *        own fields -> an unknown kind is refused rather than defaulted
 */
export function commandArgv(repoRoot: string, request: CommandRequest): string[] {
  if (typeof repoRoot !== 'string' || repoRoot.trim() === '') {
    throw new Error('no repository selected')
  }
  const req = request as CommandRequest | undefined
  if (!req || typeof req !== 'object') throw new Error('no command given')

  switch (req.kind) {
    case 'pipeline': {
      if (!isRequirementPath(req.requirement)) {
        throw new Error(`not a requirement path: ${JSON.stringify(req.requirement)}`)
      }
      return ['pipeline', '--repo', repoRoot, '--requirement', req.requirement]
    }
    case 'resume': {
      const argv = ['pipeline', '--repo', repoRoot, '--resume-slice', sliceOf(req.sliceId)]
      if (req.implementTurns !== undefined && req.implementTurns !== null) {
        if (!isStageTurns(req.implementTurns)) {
          throw new Error(`turns must be a whole number 1..${MAX_STAGE_TURNS}`)
        }
        argv.push('--max-turns-stage', `implement=${req.implementTurns}`)
      }
      return argv
    }
    case 'merge': {
      const argv = ['pipeline', '--repo', repoRoot, '--merge', sliceOf(req.sliceId)]
      if (req.push) argv.push('--push')
      return argv
    }
    case 'discard':
      return ['pipeline', '--repo', repoRoot, '--discard', sliceOf(req.sliceId)]
    case 'graph-build':
      return ['graph', 'build', '--repo', repoRoot]
    default:
      throw new Error(`unknown command: ${JSON.stringify((req as { kind?: unknown }).kind)}`)
  }
}

/**
 * The first log line of a run: what a human would have typed, in words.
 *
 * @param request  what the renderer asked for
 * @flow  one label per kind; anything else is named rather than hidden
 */
export function commandLabel(request: CommandRequest): string {
  switch (request.kind) {
    case 'pipeline':
      return `launch ${request.requirement}`
    case 'resume':
      return request.implementTurns
        ? `resume ${request.sliceId} (implement=${request.implementTurns})`
        : `resume ${request.sliceId}`
    case 'merge':
      return request.push ? `merge + push ${request.sliceId}` : `merge ${request.sliceId}`
    case 'discard':
      return `discard ${request.sliceId}`
    case 'graph-build':
      return 'build graph'
    default:
      return 'unknown command'
  }
}

/**
 * The slice a request is about, or null for the ones that are about none.
 *
 * @param request  what the renderer asked for
 */
export function commandSliceId(request: CommandRequest): string | null {
  if (request.kind === 'resume' || request.kind === 'merge' || request.kind === 'discard') {
    return request.sliceId
  }
  return null
}

/**
 * Which kinds may not run while something else is running: all of them.
 *
 * One slot is the whole concurrency design — merge and discard against the
 * same slice can then never overlap, which is the 8/18 accident by another
 * route. Kept as a function so the reason has somewhere to live.
 *
 * @param kind  the command kind being considered
 */
export function isExclusive(kind: CommandKind): boolean {
  return (
    kind === 'pipeline' ||
    kind === 'resume' ||
    kind === 'merge' ||
    kind === 'discard' ||
    kind === 'graph-build'
  )
}

/**
 * What a finished command was, in the form the discard guard remembers.
 *
 * Pure, and here rather than next to the runner, because the renderer builds
 * this too — the guard's most important input is a merge the *app* watched
 * fail, and most merge refusals leave nothing on disk to re-read.
 *
 * @param state  a finished command
 */
export function resultOf(state: CommandState): CommandResult {
  return {
    kind: state.request.kind,
    sliceId: commandSliceId(state.request),
    exitCode: state.exitCode,
    error: state.error,
    at: state.finishedAt ?? Date.now()
  }
}

/**
 * A slice id from the renderer, checked before it can become a path or a flag.
 *
 * @param value  the candidate slice id
 */
function sliceOf(value: string): string {
  if (!isSafeSegment(value)) throw new Error(`not a slice id: ${JSON.stringify(value)}`)
  return value
}

/**
 * The two reads the pipeline domain added: what can be launched, and what
 * killed the slice that is selected.
 *
 * Both are ports of things a human already does at a terminal — `ls tasks/`,
 * and reading state.json / runs.json / telemetry.json / progress.md after a
 * death. No judgement is added: the cause of death is `recovery.py`'s
 * classification of the engine's own evidence, not a second opinion.
 *
 * Electron-free, like `aidev-store.ts`, so `node --test` can run it. The fs
 * helpers are imported from that module rather than copied — one tolerant
 * reader, one set of path guards.
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { readJsonTolerant, readTextTolerant, safeSegment, slicesRoot } from '../../../main/aidev-store'
import { isRequirementPath } from '../commands'
import { CAUSE_QUOTA, causeEvidence, classifyCause } from '../death'
import { TASKS_DIR } from '../requirement'
import { failedStage, readQuota, readStopped, secondsLeft } from '../state'
import type { ProgressNote, RequirementFile, SliceFailure } from '../types'

/** A run's `summary` is clipped at 2000 chars by record_run; keep that bound. */
const SUMMARY_MAX = 2000

/**
 * Every `tasks/*.md` of this repository, by name, as the launcher lists them.
 *
 * Returns `[]` for a repository with no `tasks/` — a folder that cannot be
 * listed is an empty launcher, not an error dialog.
 *
 * `editable` annotates rather than filters: a `tasks/*.md` the editor cannot
 * open is still a requirement somebody may launch, and quietly dropping it here
 * would take a working button away.
 *
 * @param repoRoot  the repository the app has open
 * @flow  read the directory -> keep .md files -> title from the first heading
 *        -> sort by name so the list does not reshuffle between polls
 */
export function listRequirements(repoRoot: string): RequirementFile[] {
  const dir = join(repoRoot, TASKS_DIR)
  let names: string[]
  try {
    // `isFile()` is also the rule that keeps `tasks/specs/` — the umbrella
    // specs — out of the launcher and out of the editor: only the top level of
    // `tasks/` is ever looked at, and a directory is never an entry.
    names = readdirSync(dir, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith('.md'))
      .map((entry) => entry.name)
  } catch {
    return []
  }

  const out: RequirementFile[] = []
  for (const name of names.sort()) {
    const file = join(dir, name)
    let bytes = 0
    let modifiedAt = 0
    try {
      const stat = statSync(file)
      bytes = stat.size
      modifiedAt = stat.mtimeMs
    } catch {
      // the file went away between the listing and the stat; still offer it
    }
    const path = `${TASKS_DIR}/${name}`
    out.push({
      path,
      name,
      title: firstHeading(file) ?? name,
      bytes,
      modifiedAt,
      editable: isRequirementPath(path)
    })
  }
  return out
}

/**
 * Why this slice is where it is: the cause, the evidence, and what it left.
 *
 * Everything is optional. `run_dir` is an absolute path into the tool's own
 * `data/`, which may simply not exist on this machine, and a missing
 * telemetry.json only costs the exact subtype — the classification still has
 * the engine's quota flag and the run's text to work from.
 *
 * @param repoRoot  the repository the app has open
 * @param sliceId   the slice the human selected
 * @flow  no state.json -> null ; find the failed stage -> its last run ->
 *        that run's telemetry subtype -> classify with the engine's own quota
 *        flag -> attach progress.md and any open usage-limit wait
 * 주요 내부 변수: entry(그 stage의 마지막 run), quota(엔진이 남긴 한도 표식)
 */
export function readSliceFailure(repoRoot: string, sliceId: string): SliceFailure | null {
  let dir: string
  try {
    dir = join(slicesRoot(repoRoot), safeSegment(sliceId))
  } catch {
    return null
  }
  const state = readJsonTolerant(join(dir, 'state.json'))
  if (!state) return null

  const stage = failedStage(state)
  const entry = lastRunFor(dir, stage) ?? {}
  const subtype = resultSubtype(entry)
  const summary = String(entry.summary ?? '').slice(0, SUMMARY_MAX)
  const quotaState = readQuota(state)
  // The engine's answer, never re-derived: runs.json carries what
  // `looks_like_quota` decided, and state.quota carries a wait it opened.
  const quota = entry.quota === true || quotaState?.waiting === true
  const text = `${summary}\n${String(entry.status ?? '')}`
  const cause = classifyCause(subtype, text, quota)

  return {
    sliceId,
    cause,
    causeEvidence: causeEvidence(cause, subtype, quota),
    status: String(state.status ?? ''),
    stage,
    reason: typeof state.reason === 'string' ? state.reason : null,
    runId: typeof entry.run_id === 'string' ? entry.run_id : null,
    exitCode: typeof entry.exit_code === 'number' ? entry.exit_code : null,
    resultSubtype: subtype,
    summary,
    quotaWait:
      quotaState && (quotaState.waiting || cause === CAUSE_QUOTA)
        ? {
            resumeAt: quotaState.resumeAt,
            secondsLeft: quotaState.waiting ? secondsLeft(quotaState.resumeAt, Date.now()) : 0,
            retries: quotaState.retries,
            source: quotaState.source
          }
        : null,
    stopped: readStopped(state),
    progress: readProgress(dir)
  }
}

/**
 * `<slice>/progress.md` — what a stage had done when it ran out of turns.
 *
 * @param sliceDir  the slice's directory
 */
export function readProgress(sliceDir: string): ProgressNote {
  const path = join(sliceDir, 'progress.md')
  const text = readTextTolerant(path)
  return { path, text: text ?? '', exists: text !== null }
}

/**
 * The last run of a stage from `runs.json`, or the last run of any stage.
 *
 * @param sliceDir  the slice's directory
 * @param stage     the stage that failed, or null when none is marked
 * @flow  no readable ledger -> null ; walk it forward keeping the last match
 */
function lastRunFor(sliceDir: string, stage: string | null): Record<string, unknown> | null {
  const index = readJsonTolerant(join(sliceDir, 'runs.json'))
  const runs = index && Array.isArray(index.runs) ? index.runs : []
  let found: Record<string, unknown> | null = null
  for (const run of runs) {
    if (typeof run !== 'object' || run === null || Array.isArray(run)) continue
    const entry = run as Record<string, unknown>
    if (stage !== null && entry.stage !== stage) continue
    found = entry
  }
  return found
}

/**
 * `exact.result_subtype` of a run's telemetry, or null when it is out of reach.
 *
 * @param entry  a runs.json entry (possibly empty)
 */
function resultSubtype(entry: Record<string, unknown>): string | null {
  const runDir = typeof entry.run_dir === 'string' ? entry.run_dir : ''
  if (!runDir) return null
  const telemetry = readJsonTolerant(join(runDir, 'telemetry.json'))
  const exact = telemetry?.exact
  if (typeof exact !== 'object' || exact === null || Array.isArray(exact)) return null
  const subtype = (exact as Record<string, unknown>).result_subtype
  return typeof subtype === 'string' && subtype !== '' ? subtype : null
}

/**
 * The first `# ` heading of a markdown file, for the launcher's one-line label.
 *
 * Only the head of the file is read: a requirement is thousands of words and
 * the list needs its title.
 *
 * @param file  the markdown file
 * @flow  unreadable -> null ; first '# ' line wins ; none -> null
 */
function firstHeading(file: string): string | null {
  if (!existsSync(file)) return null
  let head: string
  try {
    head = readFileSync(file, 'utf8').slice(0, 4000)
  } catch {
    return null
  }
  for (const raw of head.split(/\r\n|\r|\n/)) {
    const line = raw.trim()
    if (line.startsWith('# ')) return line.slice(2).trim()
  }
  return null
}

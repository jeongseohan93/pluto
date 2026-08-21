/**
 * Reads and writes one `tasks/<name>.md` of the open repository — and nothing
 * else that can be spelled.
 *
 * This is the second capability that writes into the target repo (the first is
 * `writeApproval`), and it is deliberately the narrower of the two: the only
 * paths it will touch are the ones `commands.ts`'s requirement regex accepts,
 * which is the same set the launcher offers. `tasks/specs/umbrella.md` has a
 * separator where the regex has none, so the umbrella specs are out by the
 * shape of the rule rather than by a list of exceptions.
 *
 * The guard is applied twice on purpose, exactly as `source-store.ts` does it:
 * once on the spelling before the disk is touched, once on the resolved
 * absolute path afterwards.
 *
 * Nothing here throws. A refusal is a value with a reason in it — the editor
 * awaits an answer, and a rejected promise would be a blank panel.
 */
import { existsSync, mkdirSync, readFileSync, statSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { join, resolve, sep } from 'node:path'
import { writeTextAtomic } from '../../../main/aidev-store'
import { isRequirementPath } from '../commands'
import { TASKS_DIR, requirementCommitMessage } from '../requirement'
import type {
  RequirementDoc,
  RequirementProblem,
  RequirementSave,
  RequirementSaveInput
} from '../types'

/** A requirement is a few thousand characters. Half a megabyte is not one. */
export const MAX_REQUIREMENT_BYTES = 512 * 1024

/** git is asked one thing at a time and never interactively; 20s is generous. */
const GIT_TIMEOUT_MS = 20_000

/**
 * One `tasks/<name>.md` of this repository, or why it could not be opened.
 *
 * @param repoRoot  the repository the app has open
 * @param relPath   the path the renderer asked for
 * @flow  not a requirement path -> refused, before any disk is touched ; the
 *        resolved path leaving the root -> refused again ; absent or not a file
 *        -> missing ; past MAX_REQUIREMENT_BYTES -> too-large ; otherwise the
 *        text with any BOM removed
 * 주요 내부 변수: rel(구분자를 / 로 맞춘 상대 경로), abs(루트 안이라고 확인된 절대 경로)
 */
export function readRequirement(repoRoot: string, relPath: string): RequirementDoc {
  const rel = typeof relPath === 'string' ? relPath.replace(/\\/g, '/').trim() : ''
  if (!isRequirementPath(rel)) {
    return fail(rel, 'refused', `not a requirement of this repository: ${rel || '(none)'}`)
  }

  try {
    const abs = inside(repoRoot, rel)
    if (abs === null) return fail(rel, 'refused', `path escapes ${resolve(repoRoot)}`)

    let stat: ReturnType<typeof statSync>
    try {
      stat = statSync(abs)
    } catch {
      return fail(rel, 'missing', `no such requirement in this repository: ${rel}`)
    }
    if (!stat.isFile()) return fail(rel, 'missing', `not a file: ${rel}`)
    if (stat.size > MAX_REQUIREMENT_BYTES) {
      const detail = `${rel} is ${stat.size} bytes; the editor stops at ${MAX_REQUIREMENT_BYTES}`
      return fail(rel, 'too-large', detail)
    }

    const raw = readFileSync(abs, 'utf8')
    const text = raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw
    return {
      ok: true,
      path: rel,
      name: rel.slice(rel.lastIndexOf('/') + 1),
      text,
      bytes: stat.size
    }
  } catch (err) {
    return fail(rel, 'unreadable', String(err))
  }
}

/**
 * Write one `tasks/<name>.md` and commit that one file.
 *
 * `ok` reports the write, not the commit. A machine with no git on its PATH, or
 * a folder that is not a repository, still gets its requirement saved — and can
 * still launch it, because `pipeline.py` copies the file into the slice rather
 * than reading it out of a commit. The commit failure is reported beside the
 * save so nothing is claimed that did not happen.
 *
 * @param repoRoot  the repository the app has open
 * @param input     the path, the new body, and whether this must be a new file
 * @flow  not a requirement path -> refused ; escapes the root -> refused ;
 *        `create` over an existing file -> refused with the file untouched ;
 *        otherwise normalise the newlines -> mkdir tasks/ -> atomic write ->
 *        `git add` + `git commit -- <path>` -> report both halves separately
 * 주요 내부 변수: body(LF로 맞추고 끝 개행을 보장한 본문), commit(커밋 결과)
 */
export function saveRequirement(repoRoot: string, input: RequirementSaveInput): RequirementSave {
  const rel = typeof input?.path === 'string' ? input.path.replace(/\\/g, '/').trim() : ''
  if (!isRequirementPath(rel)) {
    return refused(rel, `not a requirement of this repository: ${rel || '(none)'}`)
  }

  const abs = inside(repoRoot, rel)
  if (abs === null) return refused(rel, `path escapes ${resolve(repoRoot)}`)

  if (input.create === true && existsSync(abs)) {
    return { ...refused(rel, `${rel} 는 이미 있습니다`), alreadyExists: true }
  }

  // CRLF is normalised away rather than preserved: a requirement written in this
  // editor on Windows would otherwise land as a diff of every line it has.
  const text = typeof input.text === 'string' ? input.text : ''
  const lf = text.replace(/\r\n/g, '\n')
  const body = lf === '' || lf.endsWith('\n') ? lf : `${lf}\n`

  try {
    mkdirSync(join(resolve(repoRoot), TASKS_DIR), { recursive: true })
    writeTextAtomic(abs, body)
  } catch (err) {
    return refused(rel, String(err))
  }

  const commit = commitPath(repoRoot, rel, requirementCommitMessage(rel))
  return { ok: true, path: rel, ...commit }
}

// --------------------------------------------------------------------- git

/** What one commit attempt came to. */
interface CommitReport {
  committed: boolean
  commit: string | null
  unchanged?: boolean
  commitError?: string
}

/**
 * `git add` then `git commit -- <path>`: this one file, whatever else is staged.
 *
 * The pathspec is the point. A human's checkout can have anything in its index,
 * and a bare `git commit -m` would sweep all of it into a commit they did not
 * ask for. With a pathspec git commits that path and leaves the index alone.
 *
 * @param repoRoot  the repository the app has open
 * @param rel       the repo-relative file to stage and commit
 * @param message   the commit subject
 * @flow  add fails (not a repo, no git) -> not committed, with the reason ;
 *        commit says "nothing to commit" -> not committed, and that is not an
 *        error ; commit fails otherwise -> the reason ; success -> the short sha
 */
function commitPath(repoRoot: string, rel: string, message: string): CommitReport {
  const add = git(repoRoot, ['add', '--', rel])
  if (!add.ok) return { committed: false, commit: null, commitError: add.error }

  const done = git(repoRoot, ['commit', '-m', message, '--', rel])
  if (!done.ok) {
    // An unchanged file is the ordinary case for "save" pressed twice: git
    // prints its whole status and exits non-zero, and the sentence that says so
    // is the *last* line of it — hence `raw` rather than the trimmed reason.
    if (/nothing to commit|nothing added to commit|no changes added/i.test(done.raw)) {
      return { committed: false, commit: null, unchanged: true }
    }
    return { committed: false, commit: null, commitError: done.error }
  }

  const head = git(repoRoot, ['rev-parse', '--short', 'HEAD'])
  return { committed: true, commit: head.ok ? head.out.trim() || null : null }
}

/** One git invocation's outcome, flattened so no caller reads a status code. */
interface GitRun {
  ok: boolean
  out: string
  /** stdout and stderr as they came, for the callers that must read all of it. */
  raw: string
  /** The same, trimmed to the first few lines — a sentence for a human. */
  error: string
}

/**
 * Run git in a repository, with the same preamble the Python side uses.
 *
 * `shell: false`, like every other spawn in this app: `rel` has already passed
 * the requirement regex, and no part of this argv is ever handed to a shell.
 *
 * @param repoRoot  the working directory for the call
 * @param args      the git arguments, after the `-c` preamble
 * @flow  git missing from PATH -> a sentence, never a throw ; non-zero -> the
 *        first lines of what git said ; zero -> its stdout
 */
function git(repoRoot: string, args: string[]): GitRun {
  const proc = spawnSync('git', [...preamble(repoRoot), ...args], {
    cwd: repoRoot,
    shell: false,
    windowsHide: true,
    encoding: 'utf8',
    timeout: GIT_TIMEOUT_MS
  })
  if (proc.error) {
    const code = (proc.error as Error & { code?: string }).code
    const reason = code === 'ENOENT' ? 'git 을 찾을 수 없습니다 (PATH)' : String(proc.error)
    return { ok: false, out: '', raw: reason, error: reason }
  }
  const out = proc.stdout ?? ''
  const raw = `${proc.stderr ?? ''}\n${out}`
  if (proc.status === 0) return { ok: true, out, raw, error: '' }
  return { ok: false, out, raw, error: firstLines(raw) }
}

/** `user.name` / `user.email`, asked once per repository. */
const IDENTITY = new Map<string, string[]>()

/**
 * The `-c` flags every call carries: never sign, and fall back to an identity
 * only where git has none of its own.
 *
 * A transcription of `aidev/workspace.py` `_base_args` / `identity_args`, and
 * for its reason: a machine that never configured a user must not have its
 * save die on `Please tell me who you are`, and a machine that *has* one must
 * go on using it.
 *
 * @param repoRoot  the repository whose config is asked
 * @flow  cached -> that ; otherwise ask git for each setting and remember the
 *        flags only the empty ones need
 */
function preamble(repoRoot: string): string[] {
  const key = repoRoot.toLowerCase()
  const cached = IDENTITY.get(key)
  if (cached) return ['-c', 'commit.gpgsign=false', ...cached]

  const flags: string[] = []
  for (const [setting, fallback] of [
    ['user.name', 'aidev'],
    ['user.email', 'aidev@localhost']
  ]) {
    const proc = spawnSync('git', ['config', '--get', setting], {
      cwd: repoRoot,
      shell: false,
      windowsHide: true,
      encoding: 'utf8',
      timeout: GIT_TIMEOUT_MS
    })
    const value = proc.error ? '' : (proc.stdout ?? '').trim()
    if (proc.error || value === '') flags.push('-c', `${setting}=${fallback}`)
  }
  IDENTITY.set(key, flags)
  return ['-c', 'commit.gpgsign=false', ...flags]
}

// ------------------------------------------------------------------ guards

/**
 * The absolute path of a repo-relative file, or null when it would leave the root.
 *
 * The belt to the regex's braces: shape is checked before this, and this
 * refuses whatever shape did not catch. Same guard as `source-store.ts`.
 *
 * @param repoRoot  the repository the app has open
 * @param rel       the repo-relative path, already checked by shape
 */
function inside(repoRoot: string, rel: string): string | null {
  const base = resolve(repoRoot)
  const abs = resolve(base, rel)
  return abs.startsWith(base + sep) ? abs : null
}

/**
 * A read refusal in the shape of an answer.
 *
 * @param path     what was asked for
 * @param problem  which refusal it is
 * @param detail   the sentence the editor shows
 */
function fail(path: string, problem: RequirementProblem, detail: string): RequirementDoc {
  return { ok: false, problem, detail, path, name: '', text: '', bytes: 0 }
}

/**
 * A save refusal: nothing was written, and nothing was committed.
 *
 * @param path   what was asked for
 * @param error  the sentence the editor shows
 */
function refused(path: string, error: string): RequirementSave {
  return { ok: false, path, error, committed: false, commit: null }
}

/**
 * The first three lines of what git complained about, as one sentence.
 *
 * @param text  git's stderr and stdout, in that order
 */
function firstLines(text: string): string {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== '')
  return lines.slice(0, 3).join(' / ') || 'git failed'
}

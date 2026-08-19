/**
 * The pipeline domain's own vocabulary: launching a requirement, watching the
 * run, and finishing a slice (resume / merge / discard).
 *
 * Nothing here is invented. Every field is something `aidev` already writes to
 * `state.json` / `runs.json` or already accepts on its command line — this file
 * only gives those things TypeScript names so the UI and the main process
 * cannot drift apart about them.
 */
import type { LogLine } from '../../shared/ide'

// ------------------------------------------------------------------ launching

/** One `tasks/*.md` a human may launch, as the repo lists it. */
export interface RequirementFile {
  /** Repo-relative, always `tasks/<name>.md` — the form the CLI is given. */
  path: string
  name: string
  /** The file's first `# ` heading, or the filename when it has none. */
  title: string
  bytes: number
  modifiedAt: number
}

export type CommandKind = 'pipeline' | 'resume' | 'merge' | 'discard' | 'graph-build'

/**
 * What the renderer is allowed to ask for. Deliberately not a command line:
 * the renderer names an intent, main turns it into argv (see `commands.ts`),
 * and there is no shape of this type that can smuggle a shell in.
 */
export type CommandRequest =
  | { kind: 'pipeline'; requirement: string }
  | { kind: 'resume'; sliceId: string; implementTurns?: number }
  | { kind: 'merge'; sliceId: string; push: boolean }
  | { kind: 'discard'; sliceId: string }
  | { kind: 'graph-build' }

// ------------------------------------------------------------------- watching

/** A running (or finished) command, without its log. Small enough to push. */
export interface CommandHead {
  /** New for every start, so a stale event can be told from a current one. */
  id: string
  request: CommandRequest
  label: string
  argv: string[]
  startedAt: number
  finishedAt: number | null
  running: boolean
  exitCode: number | null
  signal: string | null
  /** spawn itself failed (ENOENT is the one that actually happens). */
  error: string | null
  /** How many log lines this command has ever produced. */
  seq: number
}

export interface CommandState extends CommandHead {
  /** The tail of the log — older lines are dropped, `seq` still counts them. */
  lines: LogLine[]
}

/**
 * One push from main. `from` is what `seq` was before these lines: a renderer
 * whose own count does not match has missed something and re-reads the state
 * rather than showing a log with a hole in it.
 */
export interface CommandEvent {
  head: CommandHead
  from: number
  appended: LogLine[]
}

export interface CommandStart {
  ok: boolean
  /** Something else is already running — one aidev at a time, by design. */
  busy?: boolean
  error?: string
  state: CommandState | null
}

/** What a finished command was, kept by the UI so a guard can look back at it. */
export interface CommandResult {
  kind: CommandKind
  sliceId: string | null
  exitCode: number | null
  error: string | null
  at: number
}

// -------------------------------------------------------------- finishing up

/** `state["merge"]["push"]` — the backup, which can fail on its own. */
export interface SliceMergePush {
  status: string
  remote: string | null
  error: string | null
}

/** `state["merge"]`, as `merge_slice` writes it (`merged` or `conflict`). */
export interface SliceMerge {
  status: string
  at: string | null
  branch: string | null
  base: string | null
  commit: string | null
  conflicts: string[]
  push: SliceMergePush | null
}

/** `state["quota"]` — only the current wait; the history is in `recovery`. */
export interface SliceQuota {
  waiting: boolean
  resumeAt: string | null
  retries: number
  source: string | null
}

/** `state["recovery"]["stopped"]` — which safety pin ended the automation. */
export interface SliceStopped {
  at: string | null
  stage: string | null
  pin: string
  detail: string
}

// ----------------------------------------------------------------- the death

/** `aidev/recovery.py` CAUSE_QUOTA / CAUSE_TURNS / CAUSE_OTHER. */
export type DeathCause = 'usage_limit' | 'turns' | 'other'

/** How long is left of a usage-limit wait, counted at read time. */
export interface QuotaWait {
  resumeAt: string | null
  secondsLeft: number
  retries: number
  source: string | null
}

/** What a stage had done when it died. `<slice>/progress.md`. */
export interface ProgressNote {
  path: string
  text: string
  exists: boolean
}

/** Why a slice is `failed` (or `quota_wait`), assembled from what it left behind. */
export interface SliceFailure {
  sliceId: string
  cause: DeathCause
  /** Which reading produced that cause — a guess is named as a guess. */
  causeEvidence: string
  status: string
  stage: string | null
  reason: string | null
  runId: string | null
  exitCode: number | null
  resultSubtype: string | null
  summary: string
  quotaWait: QuotaWait | null
  stopped: SliceStopped | null
  progress: ProgressNote
}

// ------------------------------------------------------------------- bridge

/** The pipeline half of the renderer's capability surface. */
export interface PipelineBridge {
  getRequirements(): Promise<RequirementFile[]>
  /** Runs one named CLI invocation. Main builds the argv; there is no `exec`. */
  runCommand(request: CommandRequest): Promise<CommandStart>
  stopCommand(): Promise<CommandState | null>
  getCommandState(): Promise<CommandState | null>
  /** Returns its own unsubscribe, because a React effect needs one. */
  onCommandEvent(handler: (event: CommandEvent) => void): () => void
  getSliceFailure(sliceId: string): Promise<SliceFailure | null>
}

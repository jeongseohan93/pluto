/**
 * One `aidev` process at a time, and its output as it arrives.
 *
 * This is the whole of "발사 버튼": `spawn(aidev, argv)` with `shell: false`,
 * stdout and stderr split into lines, and a snapshot the renderer can poll or
 * subscribe to. No pipeline logic is reimplemented here — the CLI is the engine
 * and this is the button that presses it.
 *
 * **One slot, deliberately.** The daily path (launch → approve → watch → merge
 * → discard) is sequential, and a single slot makes "discard while a merge is
 * still running" structurally impossible rather than merely unlikely. Two slots
 * would reopen exactly the race the 8/18 accident came out of.
 *
 * The child is tied to the app's lifetime, the way a run is tied to the
 * terminal it was launched from. That is why the panel says so out loud.
 *
 * Electron-free: the runner takes a callback and the caller decides that it
 * means "send this to every window".
 */
import { spawn, type ChildProcess } from 'node:child_process'
import { delimiter, join } from 'node:path'
import type { LogLine } from '../../../shared/ide'
import { commandArgv, commandLabel } from '../commands'
import type {
  CommandEvent,
  CommandHead,
  CommandRequest,
  CommandStart,
  CommandState
} from '../types'

/** How much of the log is kept. Older lines are dropped; `seq` still counts them. */
const MAX_LINES = 2000

/** At most ten pushes a second — a chatty stage must not flood the renderer. */
const FLUSH_MS = 100

/** Where a GUI has to look, because it never saw the login shell's PATH. */
const FALLBACK_DIRS = ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin']

/** The names an `aidev` executable goes by, across platforms. */
const BIN_NAMES = ['aidev', 'aidev.exe', 'aidev.cmd']

/** Lines that are a failure however calmly they are phrased. */
const ERROR_MARKERS = ['MERGE CONFLICT', 'PUSH FAILED', 'FAILED', 'error:', 'Traceback']

/** Lines worth showing as an outcome rather than as chatter. */
const OK_MARKERS = ['DONE', 'merged ', 'pushed ', 'PASS']

/**
 * Which `aidev` to run.
 *
 * A GUI launched from Finder or the Dock does not inherit the login shell's
 * PATH, so "it works in my terminal" is not evidence that spawn will find it.
 * `AIDEV_BIN` is the explicit answer; the rest is where it usually is.
 *
 * @param env     the environment the app was started with
 * @param exists  how to ask whether a path is there (injected, so it is testable)
 * @flow  AIDEV_BIN wins outright -> every PATH entry -> the usual install dirs
 *        -> give up and return the bare name so the failure is a real ENOENT
 */
export function resolveAidevBin(
  env: Record<string, string | undefined>,
  exists: (path: string) => boolean
): string {
  const explicit = String(env.AIDEV_BIN ?? '').trim()
  if (explicit !== '') return explicit

  for (const dir of String(env.PATH ?? '').split(delimiter)) {
    if (!dir) continue
    for (const name of BIN_NAMES) {
      const candidate = join(dir, name)
      if (exists(candidate)) return candidate
    }
  }

  const home = String(env.HOME ?? '').trim()
  const dirs = home ? [...FALLBACK_DIRS, join(home, '.local', 'bin')] : FALLBACK_DIRS
  for (const dir of dirs) {
    for (const name of BIN_NAMES) {
      const candidate = join(dir, name)
      if (exists(candidate)) return candidate
    }
  }
  return 'aidev'
}

/**
 * Which tone a log line is shown in.
 *
 * @param text    the line as the process wrote it
 * @param stderr  did it come from stderr?
 * @flow  a failure marker anywhere -> error ; stderr -> warn ; an outcome
 *        marker -> ok ; anything else -> info
 */
export function levelFor(text: string, stderr: boolean): LogLine['level'] {
  if (ERROR_MARKERS.some((marker) => text.includes(marker))) return 'error'
  if (stderr) return 'warn'
  if (OK_MARKERS.some((marker) => text.includes(marker))) return 'ok'
  return 'info'
}

/**
 * The clock stamp each line carries, in the same shape the log panel expects.
 *
 * @param at  epoch milliseconds
 */
export function stampOf(at: number): string {
  const d = new Date(at)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

export class CommandRunner {
  private child: ChildProcess | null = null
  private current: CommandState | null = null
  private pending: LogLine[] = []
  private pendingFrom = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private partial: { out: string; err: string } = { out: '', err: '' }
  private counter = 0

  /**
   * @param onChange  called with every batch of new lines and the current head
   * @param binFor    how to find the executable (injected for tests)
   */
  constructor(
    private readonly onChange: (event: CommandEvent) => void,
    private readonly binFor: () => string
  ) {}

  /** The command as it stands, or null when nothing has run yet. */
  state(): CommandState | null {
    return this.current
  }

  /** Is a process running right now? Takes no arguments. */
  busy(): boolean {
    return this.child !== null
  }

  /**
   * Start one command. Refuses while another is running — one slot, by design.
   *
   * @param repoRoot  the repository it runs against, and its working directory
   * @param request   what the renderer asked for
   * @flow  busy -> refuse ; build argv (throws on anything unsafe) -> spawn ->
   *        wire stdout/stderr/exit -> the first log line is the argv itself
   */
  start(repoRoot: string, request: CommandRequest): CommandStart {
    if (this.child) {
      return { ok: false, busy: true, error: 'aidev is already running', state: this.current }
    }

    let argv: string[]
    try {
      argv = commandArgv(repoRoot, request)
    } catch (err) {
      return { ok: false, error: String(err instanceof Error ? err.message : err), state: this.current }
    }

    const bin = this.binFor()
    this.counter += 1
    const started = Date.now()
    this.current = {
      id: `cmd-${started}-${this.counter}`,
      request,
      label: commandLabel(request),
      argv,
      startedAt: started,
      finishedAt: null,
      running: true,
      exitCode: null,
      signal: null,
      error: null,
      seq: 0,
      lines: []
    }
    this.partial = { out: '', err: '' }
    this.pending = []
    this.pendingFrom = 0
    this.append(`${bin} ${argv.join(' ')}`, 'tool')

    let child: ChildProcess
    try {
      child = spawn(bin, argv, {
        cwd: repoRoot,
        shell: false,
        windowsHide: true,
        env: { ...process.env, PYTHONUNBUFFERED: '1' }
      })
    } catch (err) {
      this.finish(null, null, String(err))
      return { ok: false, error: String(err), state: this.current }
    }

    this.child = child
    child.stdout?.on('data', (chunk: unknown) => this.take(String(chunk), false))
    child.stderr?.on('data', (chunk: unknown) => this.take(String(chunk), true))
    child.on('error', (err: Error) => {
      const code = (err as Error & { code?: string }).code
      const hint =
        code === 'ENOENT' ? ` — no aidev at "${bin}". Set AIDEV_BIN to its full path.` : ''
      this.append(`${err.message}${hint}`, 'error')
      this.finish(null, null, err.message)
    })
    child.on('close', (code: number | null, signal: string | null) => {
      this.finish(code, signal, null)
    })

    return { ok: true, state: this.current }
  }

  /** Ask the running command to stop, the way Ctrl-C would. Takes no arguments. */
  stop(): void {
    this.child?.kill('SIGTERM')
  }

  /** Shut down with the app: the child dies with the window that launched it. */
  dispose(): void {
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
    this.child?.kill('SIGTERM')
    this.child = null
  }

  /**
   * Turn a chunk of output into whole lines, holding the partial last one.
   *
   * @param chunk   what arrived on the pipe
   * @param stderr  which pipe it arrived on
   * @flow  prepend what was left over -> split -> the tail stays behind
   */
  private take(chunk: string, stderr: boolean): void {
    const key = stderr ? 'err' : 'out'
    const parts = (this.partial[key] + chunk).split(/\r?\n/)
    this.partial[key] = parts.pop() ?? ''
    for (const line of parts) this.append(line, levelFor(line, stderr))
  }

  /**
   * Add one line to the log and schedule a push.
   *
   * @param text   the line
   * @param level  how it should read
   * @flow  ring-buffer the tail -> count it in seq -> queue it -> schedule
   */
  private append(text: string, level: LogLine['level']): void {
    const state = this.current
    if (!state) return
    const line: LogLine = { ts: stampOf(Date.now()), level, text }
    state.lines.push(line)
    if (state.lines.length > MAX_LINES) state.lines.splice(0, state.lines.length - MAX_LINES)
    state.seq += 1
    if (this.pending.length === 0) this.pendingFrom = state.seq - 1
    this.pending.push(line)
    this.schedule()
  }

  /**
   * Close the command out: flush what is left, and say how it ended.
   *
   * @param code    the exit code, or null when a signal ended it
   * @param signal  the signal, if any
   * @param error   a spawn-level failure, if any
   * @flow  drain the partial lines -> record the outcome -> one final push
   */
  private finish(code: number | null, signal: string | null, error: string | null): void {
    const state = this.current
    this.child = null
    if (!state || !state.running) return

    for (const key of ['out', 'err'] as const) {
      const left = this.partial[key]
      if (left) this.append(left, levelFor(left, key === 'err'))
      this.partial[key] = ''
    }

    state.running = false
    state.finishedAt = Date.now()
    state.exitCode = code
    state.signal = signal
    state.error = error
    this.append(
      error !== null
        ? `stopped: ${error}`
        : signal
          ? `stopped on ${signal}`
          : `exit ${code}`,
      error === null && code === 0 ? 'ok' : 'error'
    )
    this.flush()
  }

  /** Push at most ten times a second; the last batch always goes out. */
  private schedule(): void {
    if (this.timer) return
    this.timer = setTimeout(() => {
      this.timer = null
      this.flush()
    }, FLUSH_MS)
  }

  /** Send the queued lines with the head they belong to. Takes no arguments. */
  private flush(): void {
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
    const state = this.current
    if (!state || this.pending.length === 0) return
    const appended = this.pending
    const from = this.pendingFrom
    this.pending = []
    const head: CommandHead = {
      id: state.id,
      request: state.request,
      label: state.label,
      argv: state.argv,
      startedAt: state.startedAt,
      finishedAt: state.finishedAt,
      running: state.running,
      exitCode: state.exitCode,
      signal: state.signal,
      error: state.error,
      seq: state.seq
    }
    this.onChange({ head, from, appended })
  }
}

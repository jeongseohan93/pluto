/**
 * The running command, as the renderer sees it.
 *
 * A snapshot plus a subscription, not a poll: the log has to look like a
 * terminal, and 2s polling would make a stage's output arrive in clumps. Every
 * push carries the sequence number it starts at, so a renderer that has missed
 * anything re-reads the whole state instead of drawing a log with a hole in it.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { resultOf } from '@domains/pipeline/commands'
import type {
  CommandRequest,
  CommandResult,
  CommandStart,
  CommandState
} from '@domains/pipeline/types'

/** Same bound the runner keeps, so the two ends agree about what "the log" is. */
const MAX_LINES = 2000

export interface CommandBinding {
  state: CommandState | null
  busy: boolean
  /** The last merge this session ran — the discard guard's only witness. */
  lastMerge: CommandResult | null
  run: (request: CommandRequest) => Promise<CommandStart>
  stop: () => void
  /** A refusal (busy, unsafe request, no such slice), until the next attempt. */
  error: string | null
}

/**
 * Subscribe to the one command slot.
 *
 * @param onFinished  called once per command when it stops, whatever it was
 * @flow  read the snapshot on mount -> on each push, append when the sequence
 *        lines up and re-read when it does not -> a command that has just
 *        stopped is reported once, and a merge is remembered for the guard
 * 주요 내부 변수: stateRef(최신 상태), reported(이미 보고한 명령 id)
 */
export function useCommandState(onFinished?: (state: CommandState) => void): CommandBinding {
  const [state, setState] = useState<CommandState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastMerge, setLastMerge] = useState<CommandResult | null>(null)
  const stateRef = useRef<CommandState | null>(null)
  const reported = useRef<string | null>(null)
  const finished = useRef(onFinished)
  finished.current = onFinished

  const apply = useCallback((next: CommandState | null) => {
    stateRef.current = next
    setState(next)
    if (!next || next.running || reported.current === next.id) return
    reported.current = next.id
    if (next.request.kind === 'merge') setLastMerge(resultOf(next))
    finished.current?.(next)
  }, [])

  const reload = useCallback(() => {
    window.aidev.getCommandState().then(apply)
  }, [apply])

  useEffect(() => {
    reload()
    return window.aidev.onCommandEvent((event) => {
      const current = stateRef.current
      if (current && current.id === event.head.id && current.seq === event.from) {
        const lines = [...current.lines, ...event.appended]
        apply({ ...event.head, lines: lines.slice(Math.max(0, lines.length - MAX_LINES)) })
      } else {
        // We missed something (a push while unmounted, or a whole command).
        // Re-reading is cheaper than reasoning about what was lost.
        reload()
      }
    })
  }, [apply, reload])

  const run = useCallback(
    async (request: CommandRequest): Promise<CommandStart> => {
      setError(null)
      const start = await window.aidev.runCommand(request)
      if (!start.ok) setError(start.error ?? 'the command was refused')
      if (start.state) apply(start.state)
      return start
    },
    [apply]
  )

  const stop = useCallback(() => {
    window.aidev.stopCommand().then(apply)
  }, [apply])

  return { state, busy: state?.running === true, lastMerge, run, stop, error }
}

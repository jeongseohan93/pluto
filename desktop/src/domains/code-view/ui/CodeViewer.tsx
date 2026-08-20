import { useEffect, useRef, useState, type JSX } from 'react'
import type * as MonacoNs from 'monaco-editor'
import type { CodeTarget, SourceFile, SourceProblem } from '@domains/code-view/types'
import { EDITOR_OPTIONS, THEME, ensureMonaco, type MonacoApi } from '@domains/code-view/ui/monaco'
import { EmptyState } from '@renderer/components/primitives'
import { Icon } from '@renderer/components/Icon'

/** What each refusal is called, in the reader's terms rather than the store's. */
const PROBLEM_TITLE: Record<SourceProblem, string> = {
  'no-repo': 'No repository open',
  'not-in-graph': 'Not a file of this graph',
  unsupported: 'Not a file this viewer opens',
  missing: 'That file is not there',
  'too-large': 'Too large to open',
  binary: 'Not a text file',
  unreadable: 'Could not read that file'
}

/**
 * The summoned editor: one file, read-only, aimed at one range of it.
 *
 * The panel is a *view onto a coordinate*, not a document a reader owns. So
 * there are no tabs and no dirty state — a new target replaces what is here,
 * and the only file kept in memory is the one on screen.
 *
 * The fetch is keyed on `target.path` alone. Walking between two functions of
 * `pipeline.py` must not re-read five thousand lines; only the decoration and
 * the scroll move.
 *
 * Monaco may not load at all — see `monaco.ts` on `file://` and workers. When
 * it does not, `PlainBody` draws the same file with the same range marked. The
 * fallback is not decoration: it is what keeps this panel honest if the editor
 * never arrives.
 *
 * @param target   where the viewer is aimed, or null before anything opened it
 * @param onClose  fold the panel away
 * @flow  the editor's host div is mounted for as long as monaco is usable and
 *        every other state is drawn *over* it, because an editor whose
 *        container is unmounted mid-session goes on rendering into a detached
 *        node ; over it goes: no target -> an invitation ; nothing read yet ->
 *        a waiting line ; the read failed -> the reason ; monaco never arrived
 *        -> the plain numbered lines ; otherwise nothing, and the editor shows
 * 주요 내부 변수: source(읽어온 파일), api(monaco 또는 null), failed(에디터 포기),
 * live(monaco 사용 가능 = 호스트 div가 살아 있는 동안), over(에디터 위에 덮는 상태)
 */
export function CodeViewer({
  target,
  onClose
}: {
  target: CodeTarget | null
  onClose: () => void
}): JSX.Element {
  const [source, setSource] = useState<SourceFile | null>(null)
  const [loading, setLoading] = useState(false)
  const [api, setApi] = useState<MonacoApi | null>(null)
  const [failed, setFailed] = useState(false)

  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<MonacoNs.editor.IStandaloneCodeEditor | null>(null)
  const model = useRef<MonacoNs.editor.ITextModel | null>(null)
  const marks = useRef<MonacoNs.editor.IEditorDecorationsCollection | null>(null)

  const path = target?.path ?? ''

  // The editor itself, asked for once and only when a panel is actually open.
  useEffect(() => {
    let alive = true
    ensureMonaco().then((loaded) => {
      if (!alive) return
      if (loaded) setApi(loaded)
      else setFailed(true)
    })
    return () => {
      alive = false
    }
  }, [])

  // The file. `path` and nothing else: another function of the same file is a
  // move inside what is already loaded.
  useEffect(() => {
    if (!path) {
      setSource(null)
      return
    }
    let alive = true
    setLoading(true)
    window.aidev.getSourceFile(path).then((next) => {
      if (!alive) return
      setSource(next)
      setLoading(false)
    })
    return () => {
      alive = false
    }
  }, [path])

  /** Is monaco up and usable? The host div is mounted for exactly this long. */
  const live = api !== null && !failed

  // Create the editor over the host div. The div is never conditionally
  // unmounted while `live` holds — every waiting and refusal state is drawn
  // *over* it — because an editor whose container was swapped out from under it
  // keeps rendering into a detached node and the panel goes quietly blank.
  useEffect(() => {
    if (!live || !api || !host.current || editor.current) return
    try {
      editor.current = api.editor.create(host.current, { ...EDITOR_OPTIONS, theme: THEME })
      marks.current = editor.current.createDecorationsCollection([])
    } catch {
      // A viewer that cannot be built is a fallback, never a broken panel.
      setFailed(true)
    }
    return () => {
      marks.current?.clear()
      marks.current = null
      model.current?.dispose()
      model.current = null
      editor.current?.dispose()
      editor.current = null
    }
  }, [live, api])

  // The model, replaced whole. One at a time — the requirement rules out tabs,
  // so there is nothing to keep a second document alive for.
  useEffect(() => {
    if (!api || !editor.current || !source?.ok) return
    const next = api.editor.createModel(source.text, source.lang || undefined)
    editor.current.setModel(next)
    model.current?.dispose()
    model.current = next
  }, [api, source])

  // The aim: the range lit, and the start line brought near the top. Declared
  // after the model effect so a new file is in place before this reads it.
  useEffect(() => {
    const ed = editor.current
    const md = model.current
    if (!api || !ed || !md || !target || !source?.ok) return
    const last = md.getLineCount()
    // A stale graph can hold an `end_lineno` past the end of the file it was
    // read from, and a range outside the model is a decoration monaco drops.
    const from = Math.min(Math.max(1, target.line), last)
    const to = Math.min(Math.max(from, target.endLine), last)
    marks.current?.set([
      {
        range: new api.Range(from, 1, to, 1),
        options: {
          isWholeLine: true,
          className: 'code-range',
          linesDecorationsClassName: 'code-range-gutter'
        }
      }
    ])
    ed.revealLineNearTop(from, api.editor.ScrollType.Immediate)
    ed.setPosition({ lineNumber: from, column: 1 })
  }, [api, source, target])

  // What is on top of the editor right now, or nothing when the file is up.
  // Note that a re-read is *not* one of these: while another file loads the
  // one on screen stays readable, which is the whole point of keying the fetch
  // on the path alone.
  let over: JSX.Element | null = null
  if (!target) {
    over = (
      <EmptyState
        title="Nothing summoned yet"
        hint="Double-click a function in the graph, or press [코드 보기] in the node panel."
      />
    )
  } else if (!source) {
    over = (
      <div className="flex h-full items-center justify-center px-4 text-center">
        <p className="text-tiny text-fg-mute">Reading {target.path}…</p>
      </div>
    )
  } else if (!source.ok) {
    over = (
      <EmptyState title={PROBLEM_TITLE[source.problem ?? 'unreadable']} hint={source.detail ?? ''}>
        <p className="mt-1 font-mono text-micro text-fg-mute">{source.path}</p>
      </EmptyState>
    )
  } else if (failed) {
    over = <PlainBody source={source} target={target} />
  } else if (!api) {
    over = (
      <div className="flex h-full items-center justify-center px-4 text-center">
        <p className="text-tiny text-fg-mute">Summoning the editor…</p>
      </div>
    )
  }

  return (
    <div className="flex h-full min-w-0 flex-col">
      <Header target={target} source={source} plain={failed} loading={loading} onClose={onClose} />
      <div className="relative min-h-0 flex-1">
        {live ? <div ref={host} className="h-full w-full" /> : null}
        {over ? <div className="absolute inset-0 z-10 bg-panel">{over}</div> : null}
      </div>
    </div>
  )
}

/**
 * The panel's one bar: the way out, where we are, and how big it is.
 *
 * @param target   what the viewer is aimed at
 * @param source   the loaded file, for the size on the right
 * @param plain    monaco never arrived, and the body is the fallback
 * @param loading  a read is in flight — the only sign of one, since the file
 *                 already on screen is left alone until the next arrives
 * @param onClose  fold the panel away
 */
function Header({
  target,
  source,
  plain,
  loading,
  onClose
}: {
  target: CodeTarget | null
  source: SourceFile | null
  plain: boolean
  loading: boolean
  onClose: () => void
}): JSX.Element {
  const where = target
    ? `${target.path}:${target.line}${target.endLine > target.line ? `-${target.endLine}` : ''}`
    : ''
  return (
    <div className="flex h-7 shrink-0 items-center gap-1.5 border-b border-line px-2.5">
      <button
        type="button"
        onClick={onClose}
        title="Close the code viewer"
        aria-label="Close the code viewer"
        aria-expanded={true}
        className="shrink-0 text-fg-mute hover:text-fg-dim"
      >
        <Icon name="chevron" size={13} />
      </button>
      <span className="panel-label shrink-0">Code</span>
      <span className="min-w-0 flex-1 truncate font-mono text-micro text-fg-dim" title={target?.label || where}>
        {where}
      </span>
      {plain ? (
        <span className="shrink-0 rounded-sm bg-active px-1.5 py-px text-micro text-warn">
          monaco unavailable
        </span>
      ) : null}
      {loading ? <span className="shrink-0 text-micro text-fg-mute">reading…</span> : null}
      {source?.ok ? (
        <span className="shrink-0 font-mono text-micro text-fg-mute">
          {source.lines} lines · {Math.max(1, Math.round(source.bytes / 1024))} KB
        </span>
      ) : null}
    </div>
  )
}

/**
 * The file without monaco: numbered lines, with the target range marked.
 *
 * Same shape as `features/code/CodeSurface.tsx`, over the whole file rather
 * than an excerpt. It exists so that "load the file, scroll to the line,
 * highlight the range" is true even where the editor cannot be created.
 *
 * @param source  the loaded file
 * @param target  the range to mark and to scroll to
 * @flow  the row at `target.line` reports itself to the DOM and scrolls itself
 *        into the middle once, when it mounts
 */
function PlainBody({ source, target }: { source: SourceFile; target: CodeTarget }): JSX.Element {
  const start = useRef<HTMLTableRowElement>(null)
  const lines = source.text.split('\n')

  useEffect(() => {
    start.current?.scrollIntoView({ block: 'center' })
  }, [source, target])

  return (
    <div className="h-full overflow-auto py-2">
      <table className="w-full border-collapse font-mono text-tiny">
        <tbody>
          {lines.map((line, i) => {
            const n = i + 1
            const inRange = n >= target.line && n <= Math.max(target.line, target.endLine)
            return (
              <tr
                key={n}
                ref={n === target.line ? start : undefined}
                className={inRange ? 'bg-accent-soft' : 'hover:bg-hover'}
              >
                <td className="w-14 select-none pr-3 text-right align-top text-fg-mute">{n}</td>
                <td
                  className={`whitespace-pre pr-4 text-fg-dim select-text ${
                    inRange ? 'border-l-2 border-accent pl-1.5' : 'pl-2'
                  }`}
                >
                  {line || ' '}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

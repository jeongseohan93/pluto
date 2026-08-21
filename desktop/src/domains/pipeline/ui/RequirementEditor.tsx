import { useEffect, useRef, useState, type JSX } from 'react'
import type * as MonacoNs from 'monaco-editor'
import { EDIT_OPTIONS, THEME, ensureMonaco, type MonacoApi } from '@domains/code-view/ui/monaco'
import {
  REQUIREMENT_TEMPLATE,
  TASKS_DIR,
  requirementCommitMessage,
  requirementNameProblem,
  requirementPathFor
} from '@domains/pipeline/requirement'
import type { RequirementDoc, RequirementSave } from '@domains/pipeline/types'

/** Where the editor is aimed: a new `tasks/*.md`, or an existing one. */
export interface RequirementTarget {
  /** null = 새 요구사항. Otherwise the `tasks/<name>.md` being edited. */
  path: string | null
}

export interface RequirementEditorProps {
  target: RequirementTarget
  /** aidev is running — one slot, so saving and launching are both shut. */
  busy: boolean
  onClose: () => void
  /** A save landed: the sidebar's list is out of date. */
  onSaved: (path: string) => void
  /** Hand the saved path to the launcher that already existed. */
  onLaunch: (path: string) => void
}

/**
 * 요구사항을 IDE 안에서: write one `tasks/*.md`, commit it, launch it.
 *
 * The last piece of "IDE에서 다 된다" that was still being done in a text
 * editor outside the app. It is deliberately *not* a code editor: the only path
 * it can name is `tasks/<name>.md` (`requirementPathFor`, which is the
 * launcher's own regex), so there is no filename that turns this window into
 * one over `aidev/pipeline.py`.
 *
 * Monaco may not load — see `monaco.ts` on `file://` and workers. When it does
 * not, the same text is edited in a `<textarea>`, saved by the same button,
 * committed the same way. "IDE 안에서 완결" must not depend on the editor chunk
 * arriving.
 *
 * @param target    a new requirement, or the one being edited
 * @param busy      is a command running?
 * @param onClose   put the editor away
 * @param onSaved   a save landed — refresh the list
 * @param onLaunch  launch the saved path through the existing wiring
 * @flow  the host div is mounted for as long as monaco is usable and every
 *        other state is drawn *over* it, for the reason `CodeViewer` gives:
 *        an editor whose container is unmounted goes on rendering into a
 *        detached node ; over it goes: the read was refused -> the reason ;
 *        nothing read yet -> a waiting line ; monaco never arrived -> the
 *        textarea ; the editor still loading -> a waiting line
 * 주요 내부 변수: initial(이 문서가 열린 본문 — 모델은 이것으로만 다시 만든다),
 * savedText(디스크에 있는 본문), onDisk(저장된 경로 — 발사의 전제)
 */
export function RequirementEditor({
  target,
  busy,
  onClose,
  onSaved,
  onLaunch
}: RequirementEditorProps): JSX.Element {
  const creating = target.path === null
  const opening = creating ? REQUIREMENT_TEMPLATE : null

  const [name, setName] = useState(creating ? '' : basename(target.path ?? ''))
  const [initial, setInitial] = useState<string | null>(opening)
  const [text, setText] = useState(opening ?? '')
  const [savedText, setSavedText] = useState(opening ?? '')
  const [onDisk, setOnDisk] = useState<string | null>(target.path)
  const [refusal, setRefusal] = useState<RequirementDoc | null>(null)
  const [result, setResult] = useState<RequirementSave | null>(null)
  const [saving, setSaving] = useState(false)
  const [closing, setClosing] = useState(false)
  const [api, setApi] = useState<MonacoApi | null>(null)
  const [failed, setFailed] = useState(false)

  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<MonacoNs.editor.IStandaloneCodeEditor | null>(null)
  const model = useRef<MonacoNs.editor.ITextModel | null>(null)
  const nameBox = useRef<HTMLInputElement>(null)

  const path = creating ? requirementPathFor(name) : target.path
  const problem = creating ? requirementNameProblem(name) : ''
  const dirty = text !== savedText

  // The editor itself, asked for once and only because this panel opened.
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

  // An existing requirement's text. A new one already has the template.
  useEffect(() => {
    if (target.path === null) return
    let alive = true
    window.aidev.getRequirement(target.path).then((doc) => {
      if (!alive) return
      if (!doc.ok) {
        setRefusal(doc)
        return
      }
      setInitial(doc.text)
      setText(doc.text)
      setSavedText(doc.text)
    })
    return () => {
      alive = false
    }
  }, [target.path])

  // A new requirement is named before it is written, so that is where the
  // cursor starts. An existing one is opened to be read, so it is not.
  useEffect(() => {
    if (creating) nameBox.current?.focus()
  }, [creating])

  /** Is monaco up and usable? The host div is mounted for exactly this long. */
  const live = api !== null && !failed

  useEffect(() => {
    if (!live || !api || !host.current || editor.current) return
    try {
      editor.current = api.editor.create(host.current, { ...EDIT_OPTIONS, theme: THEME })
    } catch {
      // An editor that cannot be built is the textarea, never a broken panel.
      setFailed(true)
    }
    return () => {
      model.current?.dispose()
      model.current = null
      editor.current?.dispose()
      editor.current = null
    }
  }, [live, api])

  // The model, made once per *document* — `initial` changes when a file is
  // opened, never when one is saved, so a save does not throw away the cursor.
  useEffect(() => {
    const ed = editor.current
    if (!api || !ed || initial === null) return
    const next = api.editor.createModel(initial, 'markdown')
    ed.setModel(next)
    model.current?.dispose()
    model.current = next
    const sub = next.onDidChangeContent(() => setText(next.getValue()))
    const line = titleLine(initial)
    ed.setPosition({ lineNumber: line, column: next.getLineMaxColumn(line) })
    ed.revealLineNearTop(line)
    if (!creating) ed.focus()
    return () => {
      sub.dispose()
    }
  }, [api, initial, creating])

  /**
   * Write the file and commit it. Takes no arguments.
   *
   * @flow  no acceptable name -> nothing happens (the button is already shut) ;
   *        otherwise save with `create` set only while nothing is on disk yet,
   *        and on success move the baseline so the buffer stops being dirty
   */
  async function save(): Promise<void> {
    if (path === null || saving) return
    setSaving(true)
    try {
      const next = await window.aidev.saveRequirement({ path, text, create: onDisk === null })
      setResult(next)
      if (next.ok) {
        setSavedText(text)
        setOnDisk(next.path)
        // There is nothing left to lose, so the close button stops asking.
        setClosing(false)
        onSaved(next.path)
      }
    } finally {
      setSaving(false)
    }
  }

  /**
   * Put the editor away, asking once when there is unsaved text. Takes no
   * arguments.
   *
   * @flow  clean -> close ; dirty and not yet asked -> turn the button into the
   *        question ; asked -> close and lose the edit
   */
  function close(): void {
    if (!dirty || closing) {
      onClose()
      return
    }
    setClosing(true)
  }

  const canSave = !busy && !saving && path !== null && (dirty || onDisk === null)
  const canLaunch = !busy && !saving && onDisk !== null && !dirty

  let over: JSX.Element | null = null
  if (refusal) {
    over = (
      <div className="flex h-full flex-col items-center justify-center gap-1.5 px-6 text-center">
        <p className="text-small text-fg-dim">이 파일은 이 편집기로 열 수 없습니다</p>
        <p className="max-w-[46ch] text-tiny text-fg-mute">{refusal.detail ?? ''}</p>
        <p className="mt-1 font-mono text-micro text-fg-mute">{refusal.path}</p>
      </div>
    )
  } else if (initial === null) {
    // Before the textarea, not after it: a fallback that let somebody type into
    // an empty box would have their words replaced when the read landed.
    over = (
      <div className="flex h-full items-center justify-center px-4 text-center">
        <p className="text-tiny text-fg-mute">Reading {target.path}…</p>
      </div>
    )
  } else if (failed) {
    over = (
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        spellCheck={false}
        className="h-full w-full resize-none bg-app px-3 py-2 font-mono text-tiny leading-relaxed text-fg-dim outline-none"
      />
    )
  } else if (!api) {
    over = (
      <div className="flex h-full items-center justify-center px-4 text-center">
        <p className="text-tiny text-fg-mute">Summoning the editor…</p>
      </div>
    )
  }

  return (
    <div className="flex h-full min-w-0 flex-col bg-panel">
      <div className="flex h-7 shrink-0 items-center gap-1.5 border-b border-line px-2.5">
        <span className="panel-label shrink-0">Requirement</span>
        <input
          ref={nameBox}
          value={name}
          disabled={!creating}
          onChange={(event) => setName(event.target.value)}
          placeholder="파일 이름 (예: req-editor)"
          spellCheck={false}
          title={creating ? 'tasks/ 안의 파일 이름' : '이름 바꾸기는 이 편집기의 일이 아닙니다'}
          className="min-w-0 flex-1 rounded-sm border border-line bg-app px-1.5 py-0.5 font-mono text-micro text-fg placeholder:text-fg-mute disabled:opacity-60"
        />
        <span
          className={`min-w-0 shrink truncate font-mono text-micro ${
            path === null ? 'text-bad' : 'text-fg-mute'
          }`}
          title={path ?? problem}
        >
          {path ?? problem}
        </span>
        {dirty ? <span className="shrink-0 text-micro text-warn">●</span> : null}
        <button
          type="button"
          disabled={!canSave}
          onClick={() => void save()}
          title={
            path === null
              ? problem
              : busy
                ? 'aidev is already running'
                : `git commit -m "${requirementCommitMessage(path)}"`
          }
          className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
        >
          {saving ? '저장 중…' : '저장'}
        </button>
        <button
          type="button"
          disabled={!canLaunch}
          onClick={() => onDisk !== null && onLaunch(onDisk)}
          title={
            onDisk === null
              ? '먼저 저장하세요'
              : dirty
                ? '저장하지 않은 변경이 있습니다'
                : `aidev pipeline --requirement ${onDisk}`
          }
          className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
        >
          발사
        </button>
        <button
          type="button"
          onClick={close}
          title={closing ? '저장하지 않은 변경을 버립니다' : 'Close the requirement editor'}
          className={`shrink-0 rounded-sm border border-line px-2 py-0.5 text-micro hover:bg-hover ${
            closing ? 'text-bad' : 'text-fg-dim hover:text-fg'
          }`}
        >
          {closing ? '저장 안 함 · 닫기' : '닫기'}
        </button>
      </div>

      <div className="relative min-h-0 flex-1">
        {live ? <div ref={host} className="h-full w-full" /> : null}
        {over ? <div className="absolute inset-0 z-10 bg-app">{over}</div> : null}
      </div>

      <div className="shrink-0 border-t border-line px-2.5 py-1.5">
        {result ? <SaveLine result={result} /> : <Hint failed={failed} />}
      </div>
    </div>
  )
}

/**
 * What the save actually did — the file and the commit, said separately.
 *
 * A commit that did not happen is not a failed save: the file is on disk and
 * `aidev pipeline` copies it into the slice rather than reading it out of a
 * commit. Saying "saved" and then why there is no commit is the only reading
 * that is true of both halves.
 *
 * @param result  what `saveRequirement` reported
 * @flow  refused -> the reason, nothing was written ; committed -> the message
 *        and the sha ; unchanged -> say so ; written but not committed -> the
 *        save, and the git failure beside it in the warning colour
 */
function SaveLine({ result }: { result: RequirementSave }): JSX.Element {
  if (!result.ok) {
    return (
      <p className="font-mono text-micro text-bad" title={result.path}>
        {result.error ?? '저장하지 못했습니다'}
      </p>
    )
  }
  if (result.committed) {
    return (
      <p className="font-mono text-micro text-ok" title={result.path}>
        saved · {requirementCommitMessage(result.path)}
        {result.commit ? ` (${result.commit})` : ''}
      </p>
    )
  }
  return (
    <p className="font-mono text-micro text-warn" title={result.path}>
      saved —{' '}
      {result.unchanged
        ? '내용이 그대로라 커밋할 것이 없었습니다'
        : `커밋되지 않음: ${result.commitError ?? '이유 불명'}`}
    </p>
  )
}

/**
 * The standing line under the editor when nothing has been saved yet.
 *
 * @param failed  monaco never arrived, and this is the plain textarea
 */
function Hint({ failed }: { failed: boolean }): JSX.Element {
  return (
    <p className="truncate text-micro text-fg-mute">
      {failed ? 'monaco unavailable — 평문으로 편집합니다. ' : ''}
      저장하면 {TASKS_DIR}/ 에 쓰고 그 파일 하나만 커밋합니다.
    </p>
  )
}

/** The filename inside `tasks/<name>.md`. */
function basename(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1)
}

/**
 * The line the cursor starts on: the first `# ` heading, or the first line.
 *
 * A new requirement opens with `# 제목` waiting to be replaced, and that is the
 * first thing anybody writing one does.
 *
 * @param text  the document as it opened
 */
function titleLine(text: string): number {
  const lines = text.split('\n')
  const found = lines.findIndex((line) => line.startsWith('# '))
  return found < 0 ? 1 : found + 1
}

import { useEffect, useState, type JSX } from 'react'
import type { RequirementFile } from '@domains/pipeline/types'

/**
 * A1 — 발사, and the button that writes the thing being launched.
 *
 * The list is exactly what `tasks/` holds; Launch is exactly
 * `aidev pipeline --requirement <it>`. Nothing is chosen for the human and
 * nothing is remembered between repositories.
 *
 * The panel is drawn even when the list is empty. It used to return null then,
 * which is right for a launcher and wrong for this: a repository with no
 * requirements yet is exactly the one where [+ 새 요구사항] has to be reachable.
 *
 * @param busy       something is already running — one slot, so this is disabled
 * @param onLaunch   hand the chosen path to the command slot
 * @param onNew      open the editor on a new `tasks/*.md`
 * @param onEdit     open the editor on the chosen one
 * @param reloadKey  bumped by a save, so the list re-reads without a poll
 * @flow  read the list once per `reloadKey` -> keep the selection when it
 *        survived -> the select row only when there is something in it, and
 *        [+ 새 요구사항] either way
 */
export function LaunchPanel({
  busy,
  onLaunch,
  onNew,
  onEdit,
  reloadKey
}: {
  busy: boolean
  onLaunch: (requirement: string) => void
  onNew: () => void
  onEdit: (requirement: string) => void
  reloadKey: number
}): JSX.Element {
  const [files, setFiles] = useState<RequirementFile[] | null>(null)
  const [chosen, setChosen] = useState('')

  useEffect(() => {
    let alive = true
    window.aidev.getRequirements().then((list) => {
      if (!alive) return
      setFiles(list)
      setChosen((current) => (list.some((f) => f.path === current) ? current : (list[0]?.path ?? '')))
    })
    return () => {
      alive = false
    }
  }, [reloadKey])

  const selected = files?.find((file) => file.path === chosen) ?? null
  const empty = files !== null && files.length === 0

  return (
    <div className="border-b border-line px-2.5 py-2">
      {empty ? null : (
        <div className="flex items-center gap-1.5">
          <select
            value={chosen}
            disabled={busy || files === null}
            onChange={(event) => setChosen(event.target.value)}
            title={selected?.title ?? 'tasks/*.md'}
            className="min-w-0 flex-1 truncate rounded-sm border border-line bg-app px-1.5 py-0.5 font-mono text-micro text-fg-dim disabled:opacity-40"
          >
            {files === null ? (
              <option value="">loading…</option>
            ) : (
              files.map((file) => (
                <option key={file.path} value={file.path}>
                  {file.name}
                </option>
              ))
            )}
          </select>
          <button
            type="button"
            // A name this editor cannot spell is one it must not pretend to open.
            disabled={selected === null || !selected.editable}
            onClick={() => onEdit(chosen)}
            title={
              selected === null
                ? 'tasks/*.md'
                : selected.editable
                  ? `${chosen} 를 편집합니다`
                  : `${selected.name} 은 이 편집기가 열 수 없는 이름입니다`
            }
            className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
          >
            수정
          </button>
          <button
            type="button"
            disabled={busy || chosen === ''}
            onClick={() => onLaunch(chosen)}
            title={busy ? 'aidev is already running' : `aidev pipeline --requirement ${chosen}`}
            className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
          >
            Launch
          </button>
        </div>
      )}
      <div className={`flex items-center gap-1.5 ${empty ? '' : 'mt-1'}`}>
        <p className="min-w-0 flex-1 truncate text-micro text-fg-mute" title={selected?.title}>
          {empty ? 'tasks/ 에 아직 아무것도 없습니다.' : (selected?.title ?? '')}
        </p>
        <button
          type="button"
          onClick={onNew}
          title="tasks/ 아래에 새 requirement 를 씁니다"
          className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-fg"
        >
          + 새 요구사항
        </button>
      </div>
    </div>
  )
}

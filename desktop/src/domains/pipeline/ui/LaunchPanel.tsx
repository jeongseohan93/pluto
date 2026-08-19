import { useEffect, useState, type JSX } from 'react'
import type { RequirementFile } from '@domains/pipeline/types'

/**
 * A1 — 발사. Pick a `tasks/*.md`, press Launch, watch the Run panel.
 *
 * The list is exactly what `tasks/` holds; the button is exactly
 * `aidev pipeline --requirement <it>`. Nothing is chosen for the human and
 * nothing is remembered between repositories.
 *
 * @param busy      something is already running — one slot, so this is disabled
 * @param onLaunch  hand the chosen path to the command slot
 */
export function LaunchPanel({
  busy,
  onLaunch
}: {
  busy: boolean
  onLaunch: (requirement: string) => void
}): JSX.Element | null {
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
  }, [])

  if (files !== null && files.length === 0) return null

  const selected = files?.find((file) => file.path === chosen) ?? null

  return (
    <div className="border-b border-line px-2.5 py-2">
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
          disabled={busy || chosen === ''}
          onClick={() => onLaunch(chosen)}
          title={busy ? 'aidev is already running' : `aidev pipeline --requirement ${chosen}`}
          className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-fg disabled:opacity-40"
        >
          Launch
        </button>
      </div>
      {selected ? (
        <p className="mt-1 truncate text-micro text-fg-mute" title={selected.title}>
          {selected.title}
        </p>
      ) : null}
    </div>
  )
}

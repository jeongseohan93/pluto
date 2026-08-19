import { useState, type JSX } from 'react'
import type { ChangeSummary, DiffFile } from '@shared/ide'
import { EmptyState } from '@renderer/components/primitives'
import { DemoBadge } from '@shared/ui/DemoBadge'

/**
 * Reviewers read top-down: what changed, what it touches, which files, and only
 * then the lines. A raw `git diff` dump would invert that order. v0.0.1 mock —
 * this is not the open repository's diff.
 */
export function DiffSurface({ changes }: { changes: ChangeSummary | null }): JSX.Element {
  const [openPath, setOpenPath] = useState<string | null>(null)

  if (!changes) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-tiny text-fg-mute">Loading changes…</span>
      </div>
    )
  }

  if (changes.files.length === 0) {
    return <EmptyState title="No changes yet" hint="The agent has not modified this workspace." />
  }

  return (
    <div className="h-full overflow-y-auto">
      <section className="border-b border-line px-3 py-2.5">
        <p className="panel-label mb-2 flex items-center gap-2">
          Change summary <DemoBadge />
        </p>
        <dl className="flex flex-wrap gap-x-6 gap-y-1">
          <Count value={changes.filesChanged} label="files changed" />
          <Count value={changes.functionsModified} label="functions modified" />
          <Count value={changes.functionsAdded} label="functions added" />
          <Count value={changes.testsAdded} label="tests added" />
        </dl>
      </section>

      <section className="border-b border-line px-3 py-2.5">
        <p className="panel-label mb-2">Affected behaviour</p>
        <ul className="flex flex-wrap gap-1.5">
          {changes.affected.map((item) => (
            <li
              key={item}
              className="rounded-sm border border-line bg-raised px-1.5 py-0.5 text-micro text-fg-dim"
            >
              {item}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <p className="panel-label px-3 py-2">Files</p>
        <ul>
          {changes.files.map((file) => (
            <FileRow
              key={file.path}
              file={file}
              open={openPath === file.path}
              onToggle={() => setOpenPath((p) => (p === file.path ? null : file.path))}
            />
          ))}
        </ul>
      </section>
    </div>
  )
}

function Count({ value, label }: { value: number; label: string }): JSX.Element {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="font-mono text-base text-fg">{value}</dt>
      <dd className="text-tiny text-fg-mute">{label}</dd>
    </div>
  )
}

function FileRow({
  file,
  open,
  onToggle
}: {
  file: DiffFile
  open: boolean
  onToggle: () => void
}): JSX.Element {
  const name = file.path.split('/').pop() ?? file.path
  const dir = file.path.split('/').slice(0, -1).join('/')

  return (
    <li className="border-t border-line">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-hover"
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-1.5">
            <span className="truncate font-mono text-tiny text-fg-dim">{name}</span>
            <span className="truncate font-mono text-micro text-fg-mute" title={file.path}>
              {dir}
            </span>
            {file.change === 'added' ? (
              <span className="shrink-0 rounded-sm bg-active px-1 text-micro text-add">new</span>
            ) : null}
          </span>
          <span className="mt-0.5 block truncate text-micro text-fg-mute">
            {file.symbols.join(' · ')}
          </span>
        </span>
        <span className="shrink-0 font-mono text-micro text-add">+{file.added}</span>
        <span className="shrink-0 font-mono text-micro text-del">-{file.removed}</span>
      </button>

      {open ? (
        <div className="border-t border-line bg-app">
          <p className="px-3 py-1 font-mono text-micro text-fg-mute">{file.hunkHeader}</p>
          <pre className="overflow-x-auto pb-2 font-mono text-tiny leading-[1.5]">
            {file.lines.map((line, i) => (
              <div
                key={i}
                className={
                  line.type === 'add'
                    ? 'bg-add/12 text-fg-dim'
                    : line.type === 'del'
                      ? 'bg-del/12 text-fg-dim'
                      : 'text-fg-mute'
                }
              >
                <span className="inline-block w-6 select-none pl-3 text-fg-mute">
                  {line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' '}
                </span>
                {line.text || ' '}
              </div>
            ))}
          </pre>
        </div>
      ) : null}
    </li>
  )
}

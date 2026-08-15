import type { JSX } from 'react'
import type { EpicState, RepoState, SliceState } from '@shared/ide'
import { EmptyState } from '@renderer/components/primitives'
import { SliceStatusBadge } from '@renderer/features/pipeline/SliceStatusBadge'

export interface PipelineSidebarProps {
  repo: RepoState | null
  selectedSliceId: string | null
  onSelectSlice: (id: string) => void
  onOpenRepo: () => void
  onSelectRepo: (root: string) => void
}

/**
 * What the terminal's `--list` shows, without the terminal. Read-only: the
 * judgement stays in the Python core, and the only thing this panel can cause
 * is one approval file being written from the Plan surface.
 */
export function PipelineSidebar({
  repo,
  selectedSliceId,
  onSelectSlice,
  onOpenRepo,
  onSelectRepo
}: PipelineSidebarProps): JSX.Element {
  return (
    <>
      <RepoHeader repo={repo} onOpenRepo={onOpenRepo} onSelectRepo={onSelectRepo} />
      {!repo || !repo.root ? (
        <EmptyState
          title="No repository selected"
          hint="Open the repository whose .aidev/ directory the pipeline is writing."
        />
      ) : !repo.isAidevRepo ? (
        <EmptyState
          title="No .aidev/ here"
          hint="This is probably not the target repository — pick the folder the pipeline runs against."
        />
      ) : (
        <>
          {repo.epics.length > 0 ? <EpicsSection epics={repo.epics} /> : null}
          <SectionLabel>Slices</SectionLabel>
          {repo.slices.length === 0 ? (
            <p className="px-2.5 py-2 text-tiny text-fg-mute">No slices yet.</p>
          ) : (
            <ul className="pb-3">
              {repo.slices.map((slice) => (
                <li key={slice.id}>
                  <SliceRow
                    slice={slice}
                    selected={slice.id === selectedSliceId}
                    onSelect={() => onSelectSlice(slice.id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </>
  )
}

function RepoHeader({
  repo,
  onOpenRepo,
  onSelectRepo
}: {
  repo: RepoState | null
  onOpenRepo: () => void
  onSelectRepo: (root: string) => void
}): JSX.Element {
  const others = (repo?.recent ?? []).filter((path) => path !== repo?.root)

  return (
    <div className="border-b border-line px-2.5 py-2">
      <div className="flex items-center gap-2">
        <span
          className="min-w-0 flex-1 truncate text-tiny text-fg"
          title={repo?.root ?? undefined}
        >
          {repo?.name ?? 'No repository'}
        </span>
        <button
          type="button"
          onClick={onOpenRepo}
          className="shrink-0 rounded-sm border border-line px-1.5 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-fg"
        >
          Open…
        </button>
      </div>
      {others.length > 0 ? (
        <ul className="mt-1.5">
          {others.map((path) => (
            <li key={path}>
              <button
                type="button"
                onClick={() => onSelectRepo(path)}
                title={path}
                className="w-full truncate text-left font-mono text-micro text-fg-mute hover:text-fg-dim"
              >
                {path}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function EpicsSection({ epics }: { epics: EpicState[] }): JSX.Element {
  return (
    <>
      <SectionLabel>Epics</SectionLabel>
      <ul className="pb-2">
        {epics.map((epic) => (
          <li key={epic.id} className="px-2.5 py-1">
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-tiny text-fg-dim" title={epic.id}>
                {epic.id}
              </span>
              <SliceStatusBadge status={epic.status} />
            </div>
            {epic.slices.length > 0 ? (
              <p className="truncate font-mono text-micro text-fg-mute">
                {epic.slices.map((s) => `${s.index ?? '?'}=${s.status}`).join(' ')}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </>
  )
}

function SliceRow({
  slice,
  selected,
  onSelect
}: {
  slice: SliceState
  selected: boolean
  onSelect: () => void
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full flex-col gap-0.5 border-l-2 px-2.5 py-1.5 text-left transition-colors ${
        selected ? 'border-accent bg-accent-soft' : 'border-transparent hover:bg-hover'
      }`}
    >
      <span className="flex w-full items-center gap-2">
        <span
          className={`min-w-0 flex-1 truncate text-tiny ${selected ? 'text-fg' : 'text-fg-dim'}`}
          title={slice.id}
        >
          {slice.id}
        </span>
        <SliceStatusBadge status={slice.status} />
      </span>
      <span className="flex w-full items-center gap-2 font-mono text-micro text-fg-mute">
        <span className="min-w-0 flex-1 truncate">
          {slice.unreadable
            ? 'state.json unreadable'
            : slice.stages.map((stage) => `${stage.name}=${stage.status}`).join(' ')}
        </span>
        {slice.live?.stale ? (
          <span className="shrink-0 text-bad" title="live.json has not moved for over 10s">
            STALE
          </span>
        ) : null}
      </span>
    </button>
  )
}

function SectionLabel({ children }: { children: string }): JSX.Element {
  return (
    <div className="sticky top-0 z-10 flex h-6 items-center bg-panel px-2.5">
      <span className="panel-label">{children}</span>
    </div>
  )
}

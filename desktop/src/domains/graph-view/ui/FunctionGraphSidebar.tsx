import type { JSX } from 'react'
import type { GraphIndexResult } from '@domains/graph-view/types'
import { EmptyState } from '@renderer/components/primitives'

/**
 * Every indexed file, with how much of it carries a spec.
 *
 * The per-file `m/n spec` is the whole point of this list: coverage is a
 * property of the repository, and reading it one file at a time is how a human
 * decides where to write the next spec. Clicking a file jumps the canvas to it.
 *
 * @param index     the loaded index, or null while it loads
 * @param onPick    focus this function id in the canvas
 * @param selected  which function is focused, so its file reads as current
 */
export function FunctionGraphSidebar({
  index,
  onPick,
  selected
}: {
  index: GraphIndexResult | null
  onPick: (id: number) => void
  selected: number | null
}): JSX.Element {
  if (!index) {
    return (
      <div className="flex flex-col gap-1.5 p-2.5" aria-busy="true">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="h-3 rounded-sm bg-raised" style={{ width: `${90 - i * 12}%` }} />
        ))}
      </div>
    )
  }

  if (!index.ok) {
    return (
      <EmptyState
        title="No Function DB"
        hint={index.detail ?? 'Build the graph to see this repository as functions.'}
      />
    )
  }

  const meta = index.meta

  return (
    <>
      {meta ? (
        <div className="border-b border-line px-2.5 py-2 font-mono text-micro text-fg-mute">
          <p className="text-fg-dim">
            {meta.functions} fn · {meta.files} files
          </p>
          <p>
            spec {meta.specCovered}/{meta.specTotal}
            {meta.specTotal > 0
              ? ` (${Math.round((meta.specCovered / meta.specTotal) * 100)}%)`
              : ''}
          </p>
        </div>
      ) : null}

      <ul className="pb-3">
        {index.files.map((file) => {
          const total = file.functions.filter((fn) => !fn.isTest).length
          const covered = file.functions.filter((fn) => !fn.isTest && fn.hasSpec).length
          const holds = selected !== null && file.functions.some((fn) => fn.id === selected)
          const first = file.functions[0]
          return (
            <li key={file.path}>
              <button
                type="button"
                disabled={!first}
                onClick={() => first && onPick(first.id)}
                title={file.path}
                className={`flex w-full flex-col gap-0.5 border-l-2 px-2.5 py-1 text-left transition-colors ${
                  holds ? 'border-accent bg-accent-soft' : 'border-transparent hover:bg-hover'
                }`}
              >
                <span className={`w-full truncate text-tiny ${holds ? 'text-fg' : 'text-fg-dim'}`}>
                  {file.path.split('/').pop()}
                </span>
                <span className="flex w-full items-center gap-2 font-mono text-micro text-fg-mute">
                  <span className="min-w-0 flex-1 truncate">
                    {file.path.split('/').slice(0, -1).join('/')}
                  </span>
                  <span className="shrink-0">{file.functions.length} fn</span>
                  <span className={`shrink-0 ${covered < total ? 'text-warn' : 'text-ok'}`}>
                    {covered}/{total}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </>
  )
}

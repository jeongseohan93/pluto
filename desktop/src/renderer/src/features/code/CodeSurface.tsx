import type { JSX } from 'react'
import type { SymbolLocation } from '@renderer/lib/graph-lookup'
import { EmptyState } from '@renderer/components/primitives'
import { DemoBadge } from '@shared/ui/DemoBadge'

/**
 * Code is reached by drill-down, not by default. v0.0.1 shows the excerpt that
 * belongs to the selected symbol; a real editor component arrives in v0.0.6.
 * The excerpt is authored mock text, not a file read — hence the badge.
 */
export function CodeSurface({ location }: { location: SymbolLocation | null }): JSX.Element {
  if (!location) {
    return (
      <EmptyState
        title="No symbol selected"
        hint="Pick a node in the graph or an entry in the scope list to read its source."
      />
    )
  }

  const { file, symbol } = location
  const lines = symbol.code.split('\n')

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-7 shrink-0 items-center gap-2 border-b border-line px-3">
        <DemoBadge />
        <span className="truncate font-mono text-tiny text-fg-dim" title={file.path}>
          {file.path}
        </span>
        <span className="shrink-0 font-mono text-micro text-fg-mute">
          {symbol.startLine}–{symbol.endLine}
        </span>
        <span className="ml-auto shrink-0 rounded-sm bg-active px-1.5 py-px text-micro text-fg-mute">
          excerpt
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto py-2">
        <table className="w-full border-collapse font-mono text-tiny">
          <tbody>
            {lines.map((line, i) => (
              <tr key={i} className="hover:bg-hover">
                <td className="w-12 select-none pr-3 text-right align-top text-fg-mute">
                  {symbol.startLine + i}
                </td>
                <td className="whitespace-pre pr-4 text-fg-dim">{line || ' '}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

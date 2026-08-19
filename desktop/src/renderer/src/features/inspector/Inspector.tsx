import type { JSX, ReactNode } from 'react'
import type { CodeGraph, TestCase, WorkspaceSummary } from '@shared/ide'
import { relationsOf, type SymbolLocation } from '@renderer/lib/graph-lookup'
import { EmptyState, KeyValue, PanelHeader, RunStateBadge } from '@renderer/components/primitives'
import { APPROVAL_TEXT } from '@renderer/lib/labels'
import { Icon } from '@renderer/components/Icon'
import { DemoBadge } from '@shared/ui/DemoBadge'

/**
 * The v0.0.1 inspector, over the mock graph. The Function graph surface has its
 * own detail panel and reads the real Function DB; this one wears the badge.
 */
export function Inspector({
  workspace,
  graph,
  location,
  tests,
  onSelectSymbol
}: {
  workspace: WorkspaceSummary | undefined
  graph: CodeGraph | null
  location: SymbolLocation | null
  tests: TestCase[] | null
  onSelectSymbol: (id: string) => void
}): JSX.Element {
  return (
    <section className="flex h-full min-w-0 flex-col bg-panel">
      <PanelHeader title="Inspector" right={<DemoBadge />} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {location && graph ? (
          <SymbolDetail
            location={location}
            graph={graph}
            tests={tests}
            onSelectSymbol={onSelectSymbol}
          />
        ) : (
          <NoSelection workspace={workspace} />
        )}
      </div>
    </section>
  )
}

function NoSelection({ workspace }: { workspace: WorkspaceSummary | undefined }): JSX.Element {
  if (!workspace) return <EmptyState title="No workspace selected" />

  return (
    <>
      <Block title="Current slice">
        <p className="text-tiny text-fg-dim">{workspace.title}</p>
        <p className="mt-0.5 font-mono text-micro text-fg-mute">{workspace.slice}</p>
        <div className="mt-2 border-t border-line pt-1.5">
          <KeyValue label="Agent" value={workspace.agent} />
          <KeyValue label="Run" value={<RunStateBadge state={workspace.state} />} />
          <KeyValue label="Approval" value={APPROVAL_TEXT[workspace.approval]} />
          <KeyValue label="Changed files" value={workspace.changedFiles} />
          <KeyValue
            label="Tests"
            value={`${workspace.testsPassed} passed, ${workspace.testsFailed} failed`}
          />
        </div>
      </Block>
      <div className="px-3 py-6">
        <p className="text-tiny text-fg-mute">
          Select a node in the graph to inspect its references, tests and changes.
        </p>
      </div>
    </>
  )
}

function SymbolDetail({
  location,
  graph,
  tests,
  onSelectSymbol
}: {
  location: SymbolLocation
  graph: CodeGraph
  tests: TestCase[] | null
  onSelectSymbol: (id: string) => void
}): JSX.Element {
  const { file, symbol } = location
  const { calls, calledBy, testedBy } = relationsOf(graph, symbol.id)
  const failing = (tests ?? []).filter(
    (t) => t.covers.includes(symbol.name) && t.state === 'failed'
  )

  return (
    <>
      <Block>
        <div className="flex items-start gap-2">
          <Icon
            name={symbol.kind === 'test' ? 'test' : 'fn'}
            size={14}
            className="mt-0.5 shrink-0 text-fg-mute"
          />
          <div className="min-w-0">
            <h2 className="break-words font-mono text-base text-fg">{symbol.name}</h2>
            <p className="mt-0.5 break-all font-mono text-micro text-fg-mute">{symbol.signature}</p>
          </div>
        </div>
        <p className="mt-2 text-tiny text-fg-dim">{symbol.summary}</p>
        <div className="mt-2 border-t border-line pt-1.5">
          <KeyValue label="Kind" value={symbol.kind} />
          <KeyValue
            label="File"
            value={
              <span title={file.path} className="font-mono">
                {file.path.split('/').pop()}
              </span>
            }
          />
          <KeyValue
            label="Lines"
            value={<span className="font-mono">{`${symbol.startLine}–${symbol.endLine}`}</span>}
          />
          <KeyValue label="Changed" value={symbol.changed ? 'Modified in this slice' : 'No'} />
        </div>
      </Block>

      <RelationBlock title="Calls" items={calls} onSelectSymbol={onSelectSymbol} />
      <RelationBlock title="Called by" items={calledBy} onSelectSymbol={onSelectSymbol} />
      <RelationBlock title="Tested by" items={testedBy} onSelectSymbol={onSelectSymbol} />

      {failing.length > 0 ? (
        <Block title="Failing tests">
          <ul className="flex flex-col gap-1">
            {failing.map((t) => (
              <li key={t.id} className="text-tiny text-bad">
                {t.name}
              </li>
            ))}
          </ul>
        </Block>
      ) : null}
    </>
  )
}

function RelationBlock({
  title,
  items,
  onSelectSymbol
}: {
  title: string
  items: SymbolLocation[]
  onSelectSymbol: (id: string) => void
}): JSX.Element {
  return (
    <Block title={`${title} (${items.length})`}>
      {items.length === 0 ? (
        <p className="text-micro text-fg-mute">None</p>
      ) : (
        <ul className="-mx-1">
          {items.map(({ symbol, file }) => (
            <li key={symbol.id}>
              <button
                type="button"
                onClick={() => onSelectSymbol(symbol.id)}
                className="flex w-full items-baseline gap-2 rounded-sm px-1 py-[3px] text-left hover:bg-hover"
              >
                <span className="min-w-0 flex-1 truncate font-mono text-tiny text-fg-dim">
                  {symbol.name}
                </span>
                <span
                  className="shrink-0 truncate font-mono text-micro text-fg-mute"
                  title={file.path}
                >
                  {file.path.split('/').pop()}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Block>
  )
}

function Block({ title, children }: { title?: string; children: ReactNode }): JSX.Element {
  return (
    <section className="border-b border-line px-3 py-2.5">
      {title ? <p className="panel-label mb-1.5">{title}</p> : null}
      {children}
    </section>
  )
}

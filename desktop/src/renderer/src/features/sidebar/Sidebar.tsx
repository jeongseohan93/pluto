import { useState, type JSX } from 'react'
import type {
  ChangeSummary,
  CodeGraph,
  FileNode,
  TelemetrySnapshot,
  TestCase,
  WorkspaceSummary
} from '@shared/ide'
import { Icon } from '@renderer/components/Icon'
import { EmptyState, Meter, PanelHeader, RunStateBadge } from '@renderer/components/primitives'
import { tokens, usd } from '@renderer/lib/format'
import type { ActivityId } from '@renderer/features/activitybar/ActivityBar'
import {
  PipelineSidebar,
  type PipelineSidebarProps
} from '@renderer/features/pipeline/PipelineSidebar'
import { DemoBadge } from '@shared/ui/DemoBadge'
import { FunctionGraphSidebar } from '@domains/graph-view/ui/FunctionGraphSidebar'
import type { GraphIndexResult } from '@domains/graph-view/types'

/** Which sidebar views are served from `mock-data`, and so wear the badge. */
const DEMO_VIEWS: ActivityId[] = ['workspaces', 'graph', 'changes', 'tests', 'telemetry']

/**
 * The left panel: whichever view the activity bar has open, under one header.
 *
 * Collapsing is one button here rather than one per view — every activity is
 * the same panel, so folding it away folds all of them, and the activity bar
 * next to it is what brings it back.
 *
 * @param props  the active activity and the data every view of it reads,
 *               including `onCollapse` — fold this panel away
 * @flow  the header, then the one view the activity names
 */
export function Sidebar(props: {
  activity: ActivityId
  pipeline: PipelineSidebarProps
  functions: {
    index: GraphIndexResult | null
    selected: number | null
    onSelect: (id: number) => void
  }
  workspaces: WorkspaceSummary[]
  activeWorkspaceId: string
  onSelectWorkspace: (id: string) => void
  tree: FileNode[]
  graph: CodeGraph | null
  changes: ChangeSummary | null
  tests: TestCase[] | null
  telemetry: TelemetrySnapshot
  selectedSymbolId: string | null
  onSelectSymbol: (id: string) => void
  onCollapse: () => void
}): JSX.Element {
  const titles: Record<ActivityId, string> = {
    pipeline: 'Pipeline',
    functions: 'Function DB',
    workspaces: 'Workspaces',
    graph: 'Graph scope',
    changes: 'Changes',
    tests: 'Tests',
    telemetry: 'Telemetry'
  }
  const demo = DEMO_VIEWS.includes(props.activity)

  return (
    <aside className="flex h-full min-w-0 flex-col bg-panel">
      <PanelHeader
        title={titles[props.activity]}
        right={
          <>
            {demo ? <DemoBadge /> : null}
            <button
              type="button"
              onClick={props.onCollapse}
              title="Collapse panel"
              aria-label="Collapse panel"
              className="text-fg-mute hover:text-fg-dim"
            >
              <Icon name="chevron" size={13} className="rotate-180" />
            </button>
            <Icon name="search" size={13} className="text-fg-mute hover:text-fg-dim" />
          </>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {props.activity === 'pipeline' ? (
          <PipelineSidebar {...props.pipeline} />
        ) : props.activity === 'functions' ? (
          <FunctionGraphSidebar
            index={props.functions.index}
            selected={props.functions.selected}
            onPick={props.functions.onSelect}
          />
        ) : props.activity === 'workspaces' ? (
          <WorkspacesView {...props} />
        ) : props.activity === 'graph' ? (
          <ScopeView {...props} />
        ) : props.activity === 'changes' ? (
          <ChangesView changes={props.changes} />
        ) : props.activity === 'tests' ? (
          <TestsView tests={props.tests} />
        ) : (
          <TelemetryView telemetry={props.telemetry} />
        )}
      </div>
    </aside>
  )
}

function SectionLabel({ children }: { children: string }): JSX.Element {
  return (
    <div className="sticky top-0 z-10 flex h-6 items-center bg-panel px-2.5">
      <span className="panel-label">{children}</span>
    </div>
  )
}

function WorkspacesView({
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
  tree
}: {
  workspaces: WorkspaceSummary[]
  activeWorkspaceId: string
  onSelectWorkspace: (id: string) => void
  tree: FileNode[]
}): JSX.Element {
  return (
    <>
      <SectionLabel>Agent workspaces</SectionLabel>
      <ul className="pb-2">
        {workspaces.map((ws) => {
          const selected = ws.id === activeWorkspaceId
          return (
            <li key={ws.id}>
              <button
                type="button"
                onClick={() => onSelectWorkspace(ws.id)}
                className={`flex w-full flex-col gap-0.5 border-l-2 px-2.5 py-1.5 text-left transition-colors ${
                  selected ? 'border-accent bg-accent-soft' : 'border-transparent hover:bg-hover'
                }`}
              >
                <span className="flex w-full items-center gap-2">
                  <span
                    className={`min-w-0 flex-1 truncate text-tiny ${selected ? 'text-fg' : 'text-fg-dim'}`}
                    title={ws.title}
                  >
                    {ws.title}
                  </span>
                  <RunStateBadge state={ws.state} compact />
                </span>
                <span className="flex w-full items-center gap-2 font-mono text-micro text-fg-mute">
                  <span className="truncate">{ws.slice}</span>
                  {ws.changedFiles > 0 ? <span className="ml-auto">±{ws.changedFiles}</span> : null}
                  {ws.testsFailed > 0 ? (
                    <span className="text-bad">{ws.testsFailed} fail</span>
                  ) : null}
                </span>
              </button>
            </li>
          )
        })}
      </ul>

      <SectionLabel>Project files</SectionLabel>
      <div className="pb-3">
        <FileTree nodes={tree} depth={0} />
      </div>
    </>
  )
}

function FileTree({ nodes, depth }: { nodes: FileNode[]; depth: number }): JSX.Element {
  return (
    <ul>
      {nodes.map((node) => (
        <TreeItem key={node.path} node={node} depth={depth} />
      ))}
    </ul>
  )
}

function TreeItem({ node, depth }: { node: FileNode; depth: number }): JSX.Element {
  const [open, setOpen] = useState(true)
  const isDir = node.kind === 'dir'

  return (
    <li>
      <button
        type="button"
        onClick={() => isDir && setOpen((v) => !v)}
        title={node.path}
        className="flex h-[22px] w-full items-center gap-1.5 px-2.5 text-tiny text-fg-dim hover:bg-hover"
        style={{ paddingLeft: 10 + depth * 12 }}
      >
        {isDir ? (
          <Icon
            name="chevron"
            size={11}
            className={`shrink-0 text-fg-mute transition-transform ${open ? 'rotate-90' : ''}`}
          />
        ) : (
          <span className="w-[11px] shrink-0" />
        )}
        <Icon name={isDir ? 'folder' : 'file'} size={13} className="shrink-0 text-fg-mute" />
        <span className="min-w-0 flex-1 truncate text-left">{node.name}</span>
        {node.change ? (
          <span
            className={`shrink-0 font-mono text-micro ${node.change === 'added' ? 'text-add' : 'text-warn'}`}
          >
            {node.change === 'added' ? 'A' : 'M'}
          </span>
        ) : null}
      </button>
      {isDir && open && node.children ? <FileTree nodes={node.children} depth={depth + 1} /> : null}
    </li>
  )
}

function ScopeView({
  graph,
  selectedSymbolId,
  onSelectSymbol
}: {
  graph: CodeGraph | null
  selectedSymbolId: string | null
  onSelectSymbol: (id: string) => void
}): JSX.Element {
  if (!graph) return <LoadingRows />
  if (graph.files.length === 0) {
    return <EmptyState title="No scope resolved" hint="This workspace has no slice attached yet." />
  }

  return (
    <div className="pb-3">
      {graph.files.map((file) => (
        <div key={file.id}>
          <SectionLabel>{file.path.split('/').slice(-2).join('/')}</SectionLabel>
          <ul>
            {file.symbols.map((symbol) => {
              const selected = symbol.id === selectedSymbolId
              return (
                <li key={symbol.id}>
                  <button
                    type="button"
                    onClick={() => onSelectSymbol(symbol.id)}
                    title={symbol.signature}
                    className={`flex h-[22px] w-full items-center gap-1.5 px-2.5 text-tiny ${
                      selected ? 'bg-accent-soft text-fg' : 'text-fg-dim hover:bg-hover'
                    }`}
                  >
                    <Icon
                      name={symbol.kind === 'test' ? 'test' : 'fn'}
                      size={13}
                      className="shrink-0 text-fg-mute"
                    />
                    <span className="min-w-0 flex-1 truncate text-left font-mono">
                      {symbol.name}
                    </span>
                    {symbol.changed ? (
                      <span className="shrink-0 font-mono text-micro text-warn">M</span>
                    ) : null}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </div>
  )
}

function ChangesView({ changes }: { changes: ChangeSummary | null }): JSX.Element {
  if (!changes) return <LoadingRows />
  if (changes.files.length === 0) {
    return <EmptyState title="No changes" hint="Nothing has been modified in this workspace." />
  }

  return (
    <>
      <SectionLabel>Changed files</SectionLabel>
      <ul className="pb-3">
        {changes.files.map((file) => (
          <li key={file.path}>
            <div
              className="flex h-[22px] items-center gap-2 px-2.5 text-tiny text-fg-dim hover:bg-hover"
              title={file.path}
            >
              <Icon name="file" size={13} className="shrink-0 text-fg-mute" />
              <span className="min-w-0 flex-1 truncate">{file.path.split('/').pop()}</span>
              <span className="shrink-0 font-mono text-micro text-add">+{file.added}</span>
              <span className="shrink-0 font-mono text-micro text-del">-{file.removed}</span>
            </div>
          </li>
        ))}
      </ul>
    </>
  )
}

function TestsView({ tests }: { tests: TestCase[] | null }): JSX.Element {
  if (!tests) return <LoadingRows />
  if (tests.length === 0) {
    return <EmptyState title="No tests run" hint="This workspace has not executed a test run." />
  }

  const files = [...new Set(tests.map((t) => t.file))]
  return (
    <div className="pb-3">
      {files.map((file) => (
        <div key={file}>
          <SectionLabel>{file.split('/').pop() ?? file}</SectionLabel>
          <ul>
            {tests
              .filter((t) => t.file === file)
              .map((t) => (
                <li
                  key={t.id}
                  className="flex h-[22px] items-center gap-2 px-2.5 text-tiny text-fg-dim hover:bg-hover"
                  title={t.name}
                >
                  <span
                    className={`shrink-0 font-mono text-micro ${
                      t.state === 'passed'
                        ? 'text-ok'
                        : t.state === 'failed'
                          ? 'text-bad'
                          : 'text-fg-mute'
                    }`}
                  >
                    {t.state === 'passed' ? 'PASS' : t.state === 'failed' ? 'FAIL' : 'SKIP'}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{t.name}</span>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

function TelemetryView({ telemetry }: { telemetry: TelemetrySnapshot }): JSX.Element {
  return (
    <div className="flex flex-col gap-3 px-2.5 py-3">
      <Meter
        label="5h session usage"
        value={telemetry.sessionUsagePct}
        max={100}
        display={`${telemetry.sessionUsagePct}%`}
        tone={telemetry.sessionUsagePct > 80 ? 'warn' : 'accent'}
      />
      <Meter
        label="Context window"
        value={telemetry.contextTokens}
        max={telemetry.contextLimit}
        display={`${tokens(telemetry.contextTokens)} / ${tokens(telemetry.contextLimit)}`}
      />
      <dl className="mt-1 border-t border-line pt-2 font-mono text-micro">
        {(
          [
            ['input', tokens(telemetry.inputTokens)],
            ['cache create', tokens(telemetry.cacheCreationTokens)],
            ['cache read', tokens(telemetry.cacheReadTokens)],
            ['output', tokens(telemetry.outputTokens)],
            ['peak context', tokens(telemetry.peakContextTokens)],
            ['repeated reads', String(telemetry.repeatedReads)],
            ['cost', usd(telemetry.costUsd)]
          ] as const
        ).map(([k, v]) => (
          <div key={k} className="flex justify-between py-[3px]">
            <dt className="text-fg-mute">{k}</dt>
            <dd className="text-fg-dim">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function LoadingRows(): JSX.Element {
  return (
    <div className="flex flex-col gap-1.5 p-2.5" aria-busy="true">
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} className="h-3 rounded-sm bg-raised" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  )
}

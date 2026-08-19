import { useCallback, useEffect, useMemo, useState, type JSX } from 'react'
import { Group, Panel, Separator, usePanelRef } from 'react-resizable-panels'
import type { ApprovalDecision, ApprovalResult } from '@shared/ide'
import {
  useGlobalData,
  useRepoState,
  useStageArtifact,
  useWorkspaceData
} from '@renderer/lib/useIdeData'
import { findSymbol } from '@renderer/lib/graph-lookup'
import { TopBar } from '@renderer/features/topbar/TopBar'
import { ActivityBar, type ActivityId } from '@renderer/features/activitybar/ActivityBar'
import { Sidebar } from '@renderer/features/sidebar/Sidebar'
import { SurfacePane, type SurfaceId } from '@renderer/features/workspace/SurfacePane'
import { Inspector } from '@renderer/features/inspector/Inspector'
import { BottomPanel, type BottomTab } from '@renderer/features/bottom/BottomPanel'
import { StatusBar } from '@renderer/features/statusbar/StatusBar'
import { useCommandState } from '@domains/pipeline/ui/useCommandState'
import { useSliceFailure } from '@domains/pipeline/ui/useSliceFailure'
import { useFunctionGraph } from '@domains/graph-view/ui/useFunctionGraph'
import type { CommandRequest, CommandState } from '@domains/pipeline/types'
import mark from '@renderer/assets/pluto-mark.png'

/** Which surface each activity opens in the main pane. */
const ACTIVITY_SURFACE: Partial<Record<ActivityId, SurfaceId>> = {
  pipeline: 'plan',
  functions: 'functions',
  graph: 'graph',
  changes: 'diff',
  tests: 'test'
}

function App(): JSX.Element {
  const [activity, setActivity] = useState<ActivityId>('workspaces')
  const [workspaceId, setWorkspaceId] = useState('ws-night-role')
  const [selectedSymbolId, setSelectedSymbolId] = useState<string | null>(null)
  const [primary, setPrimary] = useState<SurfaceId>('graph')
  const [secondary, setSecondary] = useState<SurfaceId>('diff')
  // Graph-first: the main pane opens undivided so the whole graph is legible.
  // Split is one click away in the surface tab bar.
  const [splitOpen, setSplitOpen] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [bottomTab, setBottomTab] = useState<BottomTab>('run')
  const [bottomCollapsed, setBottomCollapsed] = useState(false)
  const [pipelineSliceId, setPipelineSliceId] = useState<string | null>(null)
  const [functionId, setFunctionId] = useState<number | null>(null)
  // Bumped when a command ends, so the failure panel re-reads what it left.
  const [runsDone, setRunsDone] = useState(0)

  const global = useGlobalData()
  const workspace = useWorkspaceData(workspaceId)
  const repo = useRepoState()
  const graph = useFunctionGraph()
  const bottomRef = usePanelRef()

  const location = useMemo(
    () => (workspace ? findSymbol(workspace.graph, selectedSymbolId) : null),
    [workspace, selectedSymbolId]
  )

  const repoState = repo.state
  const slices = useMemo(() => repoState?.slices ?? [], [repoState])
  const waiting = useMemo(() => slices.filter((s) => s.waitingStage !== null), [slices])
  const pipelineSlice = slices.find((s) => s.id === pipelineSliceId) ?? null

  // Approving is what this panel is for, so an open gate is what it opens on.
  // A selection that no longer exists (repo switched, slice discarded) is
  // dropped rather than left pointing at nothing.
  useEffect(() => {
    if (pipelineSliceId && slices.some((s) => s.id === pipelineSliceId)) return
    setPipelineSliceId(waiting[0]?.id ?? slices[0]?.id ?? null)
  }, [pipelineSliceId, slices, waiting])

  const artifactStage = pipelineSlice ? (pipelineSlice.waitingStage ?? 'plan') : null
  const artifact = useStageArtifact(pipelineSlice?.id ?? null, artifactStage)

  const refreshRepo = repo.refresh
  const reloadGraph = graph.reload

  // A command that has ended has changed something on disk: state.json, a
  // branch, or the graph itself. Re-read rather than wait out the next poll.
  const onCommandFinished = useCallback(
    (finished: CommandState) => {
      refreshRepo()
      setRunsDone((n) => n + 1)
      if (finished.request.kind === 'graph-build') reloadGraph()
    },
    [refreshRepo, reloadGraph]
  )
  const command = useCommandState(onCommandFinished)
  const failure = useSliceFailure(
    pipelineSlice?.id ?? null,
    pipelineSlice?.status ?? null,
    runsDone
  )

  const decide = useCallback(
    async (decision: ApprovalDecision, reason: string): Promise<ApprovalResult> => {
      if (!pipelineSlice?.waitingStage) {
        return { ok: false, path: '', error: 'no gate is open on this slice' }
      }
      const result = await window.aidev.writeApproval({
        sliceId: pipelineSlice.id,
        stage: pipelineSlice.waitingStage,
        decision,
        reason
      })
      // The pipeline polls every 3s; re-read now so the row moves without
      // waiting out our own interval on top of that.
      if (result.ok) refreshRepo()
      return result
    },
    [pipelineSlice, refreshRepo]
  )

  if (!global) return <Booting />

  const activeWorkspace = global.workspaces.find((w) => w.id === workspaceId)

  /**
   * Start one command, and open the panel that shows what it says. Every
   * button in the app comes through here, so "watching" is never optional.
   */
  function runCommand(request: CommandRequest): void {
    setBottomTab('run')
    const panel = bottomRef.current
    if (panel?.isCollapsed()) {
      panel.expand()
      setBottomCollapsed(false)
    }
    void command.run(request)
  }

  const functionsData = {
    index: graph.index,
    loading: graph.loading,
    selected: functionId,
    onSelect: setFunctionId,
    onBuild: () => runCommand({ kind: 'graph-build' }),
    busy: command.busy
  }

  const surfaceData = {
    pipeline: {
      slice: pipelineSlice,
      artifact,
      onDecide: decide,
      busy: command.busy,
      lastMerge: command.lastMerge,
      failure,
      onRun: runCommand
    },
    functions: functionsData,
    waitingCount: waiting.length,
    graph: workspace?.graph ?? null,
    changes: workspace?.changes ?? null,
    tests: workspace?.tests ?? null,
    location,
    selectedSymbolId,
    onSelectSymbol: setSelectedSymbolId
  }

  function handleActivity(id: ActivityId): void {
    setActivity(id)
    const surface = ACTIVITY_SURFACE[id]
    if (surface) setPrimary(surface)
  }

  function toggleBottom(): void {
    const panel = bottomRef.current
    if (!panel) return
    if (panel.isCollapsed()) {
      panel.expand()
      setBottomCollapsed(false)
    } else {
      panel.collapse()
      setBottomCollapsed(true)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar project={global.project} workspace={activeWorkspace} telemetry={global.telemetry} />

      <div className="flex min-h-0 flex-1">
        <ActivityBar value={activity} onChange={handleActivity} />

        <Group orientation="horizontal" className="min-w-0 flex-1">
          <Panel
            defaultSize={260}
            minSize={180}
            maxSize={420}
            groupResizeBehavior="preserve-pixel-size"
          >
            <Sidebar
              activity={activity}
              pipeline={{
                repo: repo.state,
                selectedSliceId: pipelineSliceId,
                onSelectSlice: setPipelineSliceId,
                onOpenRepo: repo.open,
                onSelectRepo: repo.select,
                busy: command.busy,
                onLaunch: (requirement) => runCommand({ kind: 'pipeline', requirement })
              }}
              functions={{
                index: graph.index,
                selected: functionId,
                onSelect: (id) => {
                  setFunctionId(id)
                  setPrimary('functions')
                }
              }}
              workspaces={global.workspaces}
              activeWorkspaceId={workspaceId}
              onSelectWorkspace={setWorkspaceId}
              tree={global.tree}
              graph={workspace?.graph ?? null}
              changes={workspace?.changes ?? null}
              tests={workspace?.tests ?? null}
              telemetry={global.telemetry}
              selectedSymbolId={selectedSymbolId}
              onSelectSymbol={setSelectedSymbolId}
            />
          </Panel>

          <Separator className="rp-separator" />

          <Panel minSize={320}>
            <Group orientation="vertical" className="h-full">
              <Panel minSize={200}>
                <Group orientation="horizontal" className="h-full">
                  <Panel minSize={280}>
                    <SurfacePane
                      surface={primary}
                      onSurfaceChange={setPrimary}
                      data={surfaceData}
                      zoom={zoom}
                      onZoomChange={setZoom}
                      onToggleSplit={() => setSplitOpen((v) => !v)}
                      splitOpen={splitOpen}
                    />
                  </Panel>
                  {splitOpen ? (
                    <>
                      <Separator className="rp-separator" />
                      <Panel minSize={280}>
                        <SurfacePane
                          surface={secondary}
                          onSurfaceChange={setSecondary}
                          data={surfaceData}
                          zoom={zoom}
                          onZoomChange={setZoom}
                        />
                      </Panel>
                    </>
                  ) : null}
                </Group>
              </Panel>

              <Separator className="rp-separator" />

              <Panel
                panelRef={bottomRef}
                defaultSize={220}
                minSize={120}
                collapsible
                collapsedSize={32}
                groupResizeBehavior="preserve-pixel-size"
              >
                <BottomPanel
                  tab={bottomTab}
                  onTabChange={setBottomTab}
                  logs={global.logs}
                  telemetry={global.telemetry}
                  collapsed={bottomCollapsed}
                  onToggleCollapsed={toggleBottom}
                  command={command.state}
                  onStop={command.stop}
                />
              </Panel>
            </Group>
          </Panel>

          <Separator className="rp-separator" />

          <Panel
            defaultSize={300}
            minSize={220}
            maxSize={460}
            groupResizeBehavior="preserve-pixel-size"
          >
            <Inspector
              workspace={activeWorkspace}
              graph={workspace?.graph ?? null}
              location={location}
              tests={workspace?.tests ?? null}
              onSelectSymbol={setSelectedSymbolId}
            />
          </Panel>
        </Group>
      </div>

      <StatusBar
        project={global.project}
        workspace={activeWorkspace}
        telemetry={global.telemetry}
        selectionLabel={location ? `${location.file.path}:${location.symbol.startLine}` : null}
        notice={command.error}
      />
    </div>
  )
}

function Booting(): JSX.Element {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2">
      <img src={mark} alt="" width={40} height={40} className="rounded-lg opacity-70" />
      <span className="text-tiny text-fg-mute">Pluto IDE</span>
    </div>
  )
}

export default App

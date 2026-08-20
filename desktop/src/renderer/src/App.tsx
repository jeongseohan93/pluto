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
import { SPLIT_DEFAULT } from '@domains/code-view/split'
import type { CommandRequest, CommandState } from '@domains/pipeline/types'
import mark from '@renderer/assets/pluto-mark.png'

/**
 * Which surface each activity opens in the main pane. The mock screens left the
 * tab bar, so graph/changes/tests are missing here on purpose: those activities
 * now change the sidebar only, because there is no main screen left to open.
 */
const ACTIVITY_SURFACE: Partial<Record<ActivityId, SurfaceId>> = {
  pipeline: 'plan',
  functions: 'functions'
}

/**
 * The shell: the activity bar, the three panels around the surfaces, and the
 * state they share. Both side panels fold away — the left one to nothing, since
 * the activity bar beside it is already a permanent icon rail and is also how
 * it comes back; the right one to a 32px strip that keeps its own chevron.
 *
 * @flow  no global data yet -> the boot mark ; otherwise the whole shell
 * 주요 내부 변수: activity(좌측 패널이 무엇을 보여주는가),
 * inspectorCollapsed(셰브런 방향 — 접힘의 진실은 패널 자신이 안다),
 * codeSplit(그래프와 코드가 나눠 갖는 비율 — 탭을 옮겨 다녀도 남도록 셸이 든다)
 */
function App(): JSX.Element {
  const [activity, setActivity] = useState<ActivityId>('workspaces')
  const [workspaceId, setWorkspaceId] = useState('ws-night-role')
  const [selectedSymbolId, setSelectedSymbolId] = useState<string | null>(null)
  const [primary, setPrimary] = useState<SurfaceId>('functions')
  const [secondary, setSecondary] = useState<SurfaceId>('plan')
  // Graph-first: the main pane opens undivided so the whole graph is legible.
  // Split is one click away in the surface tab bar.
  const [splitOpen, setSplitOpen] = useState(false)
  const [zoom, setZoom] = useState(1)
  // The graph/code divider's position. It lives here rather than in the surface
  // because a tab change unmounts that surface, and a ratio forgotten by a trip
  // to Plan is not remembered for the session. Nothing is written to disk: the
  // requirement asks for the session and no further. Same reason, same place as
  // `zoom` above.
  const [codeSplit, setCodeSplit] = useState(SPLIT_DEFAULT)
  const [bottomTab, setBottomTab] = useState<BottomTab>('run')
  const [bottomCollapsed, setBottomCollapsed] = useState(false)
  // Only the chevron's direction. Whether the panel is really collapsed is
  // asked of the panel itself — a separator dragged shut says nothing here.
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false)
  const [pipelineSliceId, setPipelineSliceId] = useState<string | null>(null)
  const [functionId, setFunctionId] = useState<number | null>(null)
  // Bumped when a command ends, so the failure panel re-reads what it left.
  const [runsDone, setRunsDone] = useState(0)

  const global = useGlobalData()
  const workspace = useWorkspaceData(workspaceId)
  const repo = useRepoState()
  const graph = useFunctionGraph()
  const bottomRef = usePanelRef()
  const sidebarRef = usePanelRef()
  const inspectorRef = usePanelRef()

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

  // Picking a node with the inspector folded away is picking something and
  // being told nothing about it. The panel comes back on its own.
  useEffect(() => {
    if (selectedSymbolId === null) return
    const panel = inspectorRef.current
    if (!panel?.isCollapsed()) return
    panel.expand()
    setInspectorCollapsed(false)
  }, [selectedSymbolId, inspectorRef])

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

  /**
   * Code has appeared beside the graph — fold the inspector away to make room.
   * Takes no arguments.
   *
   * Called at the *moment* it appears and only then (the surface's effect fires
   * on `codeOpen`'s false→true), so double-clicking a second node never folds
   * away an inspector somebody deliberately opened again. There is no path
   * back: re-opening it is the reader's to do.
   *
   * @flow  already folded -> nothing, because the panel itself owns that truth
   */
  const makeRoomForCode = useCallback((): void => {
    const panel = inspectorRef.current
    if (!panel || panel.isCollapsed()) return
    panel.collapse()
    setInspectorCollapsed(true)
  }, [inspectorRef])

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
    waitingCount: waiting.length
  }

  /**
   * Open one activity's view — and, since the sidebar collapses to nothing,
   * this rail is also how it is opened again.
   *
   * @param id  which activity was pressed
   * @flow  the open view pressed again -> fold the panel away ; anything else
   *        -> the panel is there, whether it was folded or not
   */
  function handleActivity(id: ActivityId): void {
    const panel = sidebarRef.current
    if (id === activity && panel && !panel.isCollapsed()) panel.collapse()
    else panel?.expand()
    setActivity(id)
    const surface = ACTIVITY_SURFACE[id]
    if (surface) setPrimary(surface)
  }

  /**
   * Fold the inspector away, or bring it back. Takes no arguments.
   *
   * @flow  the panel itself is asked whether it is collapsed, so a separator
   *        dragged shut and a chevron pressed mean the same thing here
   */
  function toggleInspector(): void {
    const panel = inspectorRef.current
    if (!panel) return
    const collapsed = panel.isCollapsed()
    if (collapsed) panel.expand()
    else panel.collapse()
    setInspectorCollapsed(!collapsed)
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
            panelRef={sidebarRef}
            defaultSize={260}
            minSize={180}
            maxSize={420}
            collapsible
            collapsedSize={0}
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
              onCollapse={() => sidebarRef.current?.collapse()}
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
                      codeSplit={codeSplit}
                      onCodeSplitChange={setCodeSplit}
                      onCodeSplitOpen={makeRoomForCode}
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
                          codeSplit={codeSplit}
                          onCodeSplitChange={setCodeSplit}
                          onCodeSplitOpen={makeRoomForCode}
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
            panelRef={inspectorRef}
            defaultSize={300}
            minSize={220}
            maxSize={460}
            collapsible
            collapsedSize={32}
            groupResizeBehavior="preserve-pixel-size"
          >
            <Inspector
              workspace={activeWorkspace}
              graph={workspace?.graph ?? null}
              location={location}
              tests={workspace?.tests ?? null}
              onSelectSymbol={setSelectedSymbolId}
              collapsed={inspectorCollapsed}
              onToggle={toggleInspector}
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

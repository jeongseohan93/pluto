import { useMemo, useState, type JSX } from 'react'
import { Group, Panel, Separator, usePanelRef } from 'react-resizable-panels'
import { useGlobalData, useWorkspaceData } from '@renderer/lib/useIdeData'
import { findSymbol } from '@renderer/lib/graph-lookup'
import { TopBar } from '@renderer/features/topbar/TopBar'
import { ActivityBar, type ActivityId } from '@renderer/features/activitybar/ActivityBar'
import { Sidebar } from '@renderer/features/sidebar/Sidebar'
import { SurfacePane, type SurfaceId } from '@renderer/features/workspace/SurfacePane'
import { Inspector } from '@renderer/features/inspector/Inspector'
import { BottomPanel, type BottomTab } from '@renderer/features/bottom/BottomPanel'
import { StatusBar } from '@renderer/features/statusbar/StatusBar'

/** Which surface each activity opens in the main pane. */
const ACTIVITY_SURFACE: Partial<Record<ActivityId, SurfaceId>> = {
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
  const [bottomTab, setBottomTab] = useState<BottomTab>('agent')
  const [bottomCollapsed, setBottomCollapsed] = useState(false)

  const global = useGlobalData()
  const workspace = useWorkspaceData(workspaceId)
  const bottomRef = usePanelRef()

  const location = useMemo(
    () => (workspace ? findSymbol(workspace.graph, selectedSymbolId) : null),
    [workspace, selectedSymbolId]
  )

  if (!global) return <Booting />

  const activeWorkspace = global.workspaces.find((w) => w.id === workspaceId)

  const surfaceData = {
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
      />
    </div>
  )
}

function Booting(): JSX.Element {
  return (
    <div className="flex h-full items-center justify-center">
      <span className="text-tiny tracking-[0.14em] text-fg-mute">AI DEV IDE</span>
    </div>
  )
}

export default App

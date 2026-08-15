import { useEffect, useState } from 'react'
import type {
  ChangeSummary,
  CodeGraph,
  FileNode,
  ProjectInfo,
  SessionLogs,
  TelemetrySnapshot,
  TestCase,
  WorkspaceSummary
} from '@shared/ide'

export interface GlobalData {
  project: ProjectInfo
  workspaces: WorkspaceSummary[]
  tree: FileNode[]
  telemetry: TelemetrySnapshot
  logs: SessionLogs
}

export interface WorkspaceData {
  graph: CodeGraph
  changes: ChangeSummary
  tests: TestCase[]
}

export function useGlobalData(): GlobalData | null {
  const [data, setData] = useState<GlobalData | null>(null)

  useEffect(() => {
    let alive = true
    Promise.all([
      window.aidev.getProject(),
      window.aidev.getWorkspaces(),
      window.aidev.getProjectTree(),
      window.aidev.getTelemetrySnapshot(),
      window.aidev.getSessionLogs()
    ]).then(([project, workspaces, tree, telemetry, logs]) => {
      if (alive) setData({ project, workspaces, tree, telemetry, logs })
    })
    return () => {
      alive = false
    }
  }, [])

  return data
}

/**
 * Returns null while the requested workspace is still loading. The loaded id is
 * kept next to the payload so switching workspaces reads as "loading" without a
 * synchronous reset inside the effect.
 */
export function useWorkspaceData(workspaceId: string): WorkspaceData | null {
  const [loaded, setLoaded] = useState<{ id: string; data: WorkspaceData } | null>(null)

  useEffect(() => {
    let alive = true
    Promise.all([
      window.aidev.getGraph(workspaceId),
      window.aidev.getChangeSummary(workspaceId),
      window.aidev.getTests(workspaceId)
    ]).then(([graph, changes, tests]) => {
      if (alive) setLoaded({ id: workspaceId, data: { graph, changes, tests } })
    })
    return () => {
      alive = false
    }
  }, [workspaceId])

  return loaded?.id === workspaceId ? loaded.data : null
}

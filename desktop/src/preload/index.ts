import { contextBridge, ipcRenderer } from 'electron'
import { IPC, type AidevBridge } from '../shared/ide'

/**
 * The only channel between the IDE UI and the machine.
 *
 * Every member is a named, read-only capability. There is deliberately no
 * `exec`, no `runShell`, and no `readFile(anyPath)` here — the renderer must
 * not be able to widen its own reach.
 */
const aidev: AidevBridge = {
  getProject: () => ipcRenderer.invoke(IPC.project),
  getWorkspaces: () => ipcRenderer.invoke(IPC.workspaces),
  getProjectTree: () => ipcRenderer.invoke(IPC.projectTree),
  getGraph: (workspaceId) => ipcRenderer.invoke(IPC.graph, workspaceId),
  getChangeSummary: (workspaceId) => ipcRenderer.invoke(IPC.changeSummary, workspaceId),
  getTests: (workspaceId) => ipcRenderer.invoke(IPC.tests, workspaceId),
  getTelemetrySnapshot: () => ipcRenderer.invoke(IPC.telemetry),
  getSessionLogs: () => ipcRenderer.invoke(IPC.sessionLogs)
}

contextBridge.exposeInMainWorld('aidev', aidev)

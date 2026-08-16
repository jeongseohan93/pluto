import { contextBridge, ipcRenderer } from 'electron'
import { IPC, type AidevBridge } from '../shared/ide'

/**
 * The only channel between the IDE UI and the machine.
 *
 * Every member is a named capability. There is deliberately no `exec`, no
 * `runShell`, and no `readFile(anyPath)` here — the renderer must not be able
 * to widen its own reach. Nothing is imported from `node:` either: the window
 * is sandboxed, and this file must not be the hole in it.
 *
 * Exactly one member writes into the target repository: `writeApproval`.
 */
const aidev: AidevBridge = {
  getProject: () => ipcRenderer.invoke(IPC.project),
  getWorkspaces: () => ipcRenderer.invoke(IPC.workspaces),
  getProjectTree: () => ipcRenderer.invoke(IPC.projectTree),
  getGraph: (workspaceId) => ipcRenderer.invoke(IPC.graph, workspaceId),
  getChangeSummary: (workspaceId) => ipcRenderer.invoke(IPC.changeSummary, workspaceId),
  getTests: (workspaceId) => ipcRenderer.invoke(IPC.tests, workspaceId),
  getTelemetrySnapshot: () => ipcRenderer.invoke(IPC.telemetry),
  getSessionLogs: () => ipcRenderer.invoke(IPC.sessionLogs),

  getRepoState: () => ipcRenderer.invoke(IPC.repoState),
  openRepoDialog: () => ipcRenderer.invoke(IPC.openRepo),
  selectRepo: (root) => ipcRenderer.invoke(IPC.selectRepo, root),
  getStageArtifact: (sliceId, stage) => ipcRenderer.invoke(IPC.artifact, sliceId, stage),
  // The one write. `approvals/<stage>.md` and nothing else.
  writeApproval: (input) => ipcRenderer.invoke(IPC.approve, input)
}

contextBridge.exposeInMainWorld('aidev', aidev)

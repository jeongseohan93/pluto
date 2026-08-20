import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import { IPC, type AidevBridge } from '../shared/ide'
import type { CommandEvent } from '../domains/pipeline/types'

/**
 * The only channel between the IDE UI and the machine.
 *
 * Every member is a named capability. There is deliberately no `exec`, no
 * `runShell`, and no `readFile(anyPath)` here — the renderer must not be able
 * to widen its own reach. Nothing is imported from `node:` either: the window
 * is sandboxed, and this file must not be the hole in it.
 *
 * Exactly one member writes into the target repository: `writeApproval`.
 *
 * v0.2.6 adds the ability to *start* things, and keeps the same shape while
 * doing it: `runCommand` takes a named request (launch this requirement, resume
 * this slice, merge, discard, build the graph) — never a command line. Main
 * builds the argv, checks the target exists in the open repository, and spawns
 * with `shell: false`. There is still no `exec` here, and still no way for the
 * renderer to name a path of its own.
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
  writeApproval: (input) => ipcRenderer.invoke(IPC.approve, input),

  // -- v0.2.6 the pipeline's own commands
  getRequirements: () => ipcRenderer.invoke(IPC.requirements),
  runCommand: (request) => ipcRenderer.invoke(IPC.runCommand, request),
  stopCommand: () => ipcRenderer.invoke(IPC.stopCommand),
  getCommandState: () => ipcRenderer.invoke(IPC.commandState),
  onCommandEvent: (handler) => {
    // The listener is wrapped so the renderer never touches the Electron event
    // object, and the unsubscribe is returned because a React effect needs one.
    const listener = (_event: IpcRendererEvent, payload: CommandEvent): void => handler(payload)
    ipcRenderer.on(IPC.commandEvent, listener)
    return () => {
      ipcRenderer.removeListener(IPC.commandEvent, listener)
    }
  },
  getSliceFailure: (sliceId) => ipcRenderer.invoke(IPC.sliceFailure, sliceId),

  // -- v0.2.6 the Function DB, read-only
  getGraphIndex: () => ipcRenderer.invoke(IPC.graphIndex),
  getGraphNode: (id) => ipcRenderer.invoke(IPC.graphNode, id),

  // -- v0.2.7 one source file, read-only.
  // This is not the `readFile(anyPath)` line 10 rules out. Main resolves the
  // path inside the repository it has open, refuses anything the graph does not
  // already know, and refuses anything past an extension list and a size
  // ceiling. The renderer can name a path it was shown; it cannot widen that.
  getSourceFile: (path) => ipcRenderer.invoke(IPC.sourceFile, path)
}

contextBridge.exposeInMainWorld('aidev', aidev)

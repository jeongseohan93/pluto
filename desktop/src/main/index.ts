import { app, dialog, shell, BrowserWindow, ipcMain } from 'electron'
import { basename, join } from 'path'
import { existsSync } from 'fs'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { IPC, type ApprovalInput, type ApprovalResult, type RepoState } from '../shared/ide'
import * as mock from './mock-data'
import * as store from './aidev-store'
import { addRecent, readRecents } from './recent-repos'

function createWindow(): void {
  // Create the browser window.
  const mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#0d0e10',
    title: 'Pluto IDE',
    // macOS takes its icon from the bundle; Windows and Linux need it set here
    // or the dev run shows the default Electron mark.
    ...(process.platform === 'darwin' ? {} : { icon }),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      // The renderer gets no OS reach of its own: everything it may do arrives
      // through the capability API in preload.
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // Load the remote URL for development or the local html file for production.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(() => {
  // Set app user model id for windows
  electronApp.setAppUserModelId('com.plutoide.app')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  registerIdeHandlers()
  registerRepoHandlers()

  createWindow()

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

/**
 * Read-only capability handlers backing the preload bridge.
 *
 * v0.0.1 answers every one of these from `mock-data`. When the Python `aidev`
 * core is wired in, only the right-hand side of these lines changes.
 */
function registerIdeHandlers(): void {
  ipcMain.handle(IPC.project, () => mock.project)
  ipcMain.handle(IPC.workspaces, () => mock.workspaces)
  ipcMain.handle(IPC.projectTree, () => mock.projectTree)
  ipcMain.handle(IPC.graph, (_event, workspaceId: string) => mock.graphFor(workspaceId))
  ipcMain.handle(IPC.changeSummary, (_event, workspaceId: string) =>
    mock.changeSummaryFor(workspaceId)
  )
  ipcMain.handle(IPC.tests, (_event, workspaceId: string) => mock.testsFor(workspaceId))
  ipcMain.handle(IPC.telemetry, () => mock.telemetry)
  ipcMain.handle(IPC.sessionLogs, () => mock.sessionLogs)
}

// --------------------------------------------------------- v0.2.5 real repo

/**
 * The selected repository. Main owns it — the renderer may ask for a dialog or
 * name a path it has already been given, and nothing else. A renderer that
 * could pass any string would be a read-anything capability by another name.
 */
let repoRoot: string | null = null

function recentsFile(): string {
  // userData, never inside a repo: Pluto writes one file into a repo, and this
  // is not it.
  return join(app.getPath('userData'), 'pluto-repos.json')
}

/**
 * Read `<repo>/.aidev` fresh. This is the poll target, so it must be cheap and
 * it must never throw: the renderer calls it every couple of seconds while the
 * pipeline is replacing these same files.
 */
function snapshot(): RepoState {
  const root = repoRoot
  const base: RepoState = {
    root,
    name: root ? basename(root) : null,
    isAidevRepo: false,
    recent: safely(() => readRecents(recentsFile()), [] as string[]),
    slices: [],
    epics: [],
    readAt: Date.now()
  }
  if (!root) return base
  return {
    ...base,
    isAidevRepo: safely(() => store.isAidevRepo(root), false),
    slices: safely(() => store.listSlices(root), []),
    epics: safely(() => store.listEpics(root), [])
  }
}

function adopt(root: string): RepoState {
  repoRoot = root
  safely(() => addRecent(recentsFile(), root), [] as string[])
  return snapshot()
}

function registerRepoHandlers(): void {
  // Reopen whatever was open last, if it is still there.
  const last = safely(() => readRecents(recentsFile()), [] as string[])[0]
  if (last && existsSync(last)) repoRoot = last

  ipcMain.handle(IPC.repoState, () => snapshot())

  ipcMain.handle(IPC.openRepo, async () => {
    const parent = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0]
    const picked = await dialog.showOpenDialog(parent, {
      title: 'Select an aidev repository',
      properties: ['openDirectory']
    })
    const root = picked.canceled ? null : (picked.filePaths[0] ?? null)
    return root ? adopt(root) : snapshot()
  })

  ipcMain.handle(IPC.selectRepo, (_event, root: string) => {
    // Only a path main has already handed out. Anything else is ignored.
    const recent = safely(() => readRecents(recentsFile()), [] as string[])
    const known = recent.find((path) => path.toLowerCase() === String(root).toLowerCase())
    return known ? adopt(known) : snapshot()
  })

  ipcMain.handle(IPC.artifact, (_event, sliceId: string, stage: string) => {
    if (!repoRoot) return { stage, path: '', text: '', exists: false }
    try {
      return store.readStageArtifact(repoRoot, sliceId, stage)
    } catch {
      return { stage, path: '', text: '', exists: false }
    }
  })

  ipcMain.handle(IPC.approve, (_event, input: ApprovalInput): ApprovalResult => {
    if (!repoRoot) return { ok: false, path: '', error: 'no repository selected' }
    try {
      return store.writeApproval(repoRoot, input)
    } catch (err) {
      return { ok: false, path: '', error: String(err) }
    }
  })
}

/** A failed read is an empty panel, never a broken one. */
function safely<T>(read: () => T, fallback: T): T {
  try {
    return read()
  } catch {
    return fallback
  }
}

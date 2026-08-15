import { app, shell, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { IPC } from '../shared/ide'
import * as mock from './mock-data'

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
    title: 'AI Dev IDE',
    ...(process.platform === 'linux' ? { icon } : {}),
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
  electronApp.setAppUserModelId('com.electron')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  registerIdeHandlers()

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

// Electron 主进程：拉起 lumi serve sidecar、创建窗口、经 IPC 把 ws 连接信息给 renderer。
const { app, BrowserWindow, ipcMain, shell, Notification } = require('electron')
const { spawn } = require('node:child_process')
const net = require('node:net')
const path = require('node:path')

// desktop/electron/main.cjs → desktop → Lumi 项目根
const PROJECT_ROOT = path.resolve(__dirname, '..', '..')
// 应用图标：dev 下设置 Dock/窗口图标；打包后由打包器的 icns/ico 配置接管
const APP_ICON = path.join(__dirname, '..', 'assets', 'icon.png')

let serveProc = null
let wsPort = 0
let stopping = false

// 让 OS 分配一个空闲端口
function pickPort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address()
      srv.close(() => resolve(port))
    })
  })
}

// dev：用 uv run lumi serve。打包后应替换为内置可移植 Python 运行时（见 nix/desktop 思路）。
function startSidecar(port) {
  serveProc = spawn('uv', ['run', 'lumi', 'serve', '--port', String(port)], {
    cwd: PROJECT_ROOT,
    env: { ...process.env },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  serveProc.stdout.on('data', (d) => process.stdout.write(`[lumi serve] ${d}`))
  serveProc.stderr.on('data', (d) => process.stderr.write(`[lumi serve] ${d}`))
  serveProc.on('exit', (code) => {
    console.log(`[lumi serve] 退出，code=${code}`)
    serveProc = null
    // 非主动停止（崩溃/被外部杀）时同端口自愈重启，renderer 的重连逻辑会自动连上
    if (!stopping) {
      console.log('[lumi serve] 非主动退出，2s 后重启…')
      setTimeout(() => startSidecar(port), 2000)
    }
  })
}

function stopSidecar() {
  stopping = true
  if (serveProc) {
    serveProc.kill()
    serveProc = null
  }
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 680,
    minHeight: 480,
    backgroundColor: '#1a1a19',
    icon: APP_ICON,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 16, y: 16 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // 外链（markdown 里的链接、window.open）一律走系统浏览器，避免应用窗口被导航走。
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith('http://127.0.0.1:5173') && !url.startsWith('file://')) {
      e.preventDefault()
      shell.openExternal(url)
    }
  })

  if (!app.isPackaged) {
    win.loadURL(process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173')
  } else {
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }
}

// renderer 经 preload 调用，拿到 sidecar 的 WS 地址（带重连，无需等就绪）
ipcMain.handle('lumi:connection', () => ({ wsUrl: `ws://127.0.0.1:${wsPort}/ws` }))

// renderer 调试日志透传到 dev 终端（renderer console 默认不进终端）
ipcMain.on('lumi:log', (_e, msg) => console.log('[renderer]', msg))

// 通知点击：把窗口带回前台（还原最小化 + 跨平台聚焦）
function focusMainWindow() {
  const win = BrowserWindow.getAllWindows()[0]
  if (!win) return
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
}
ipcMain.handle('lumi:focus', focusMainWindow)

// 系统通知走主进程 Notification（renderer 的 HTML5 Notification 在 macOS
// dev/未签名场景不可靠）。点击时聚焦窗口并把 tag 回传 renderer 切会话。
ipcMain.handle('lumi:notify', (event, { title, body, tag }) => {
  console.log('[notify] 请求:', title, '| supported =', Notification.isSupported())
  if (!Notification.isSupported()) return
  const n = new Notification({ title: String(title || 'Lumi'), body: String(body || '') })
  n.on('show', () => console.log('[notify] 已展示:', title))
  n.on('failed', (_e, err) => console.log('[notify] 失败:', err))
  n.on('click', () => {
    focusMainWindow()
    if (!event.sender.isDestroyed()) event.sender.send('lumi:notify-click', tag)
  })
  n.show()
})

app.whenReady().then(async () => {
  // macOS dev：Dock 图标运行时设置（打包后由 bundle 的 icns 接管）
  if (app.dock) app.dock.setIcon(APP_ICON)
  wsPort = await pickPort()
  startSidecar(wsPort)
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  stopSidecar()
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', stopSidecar)

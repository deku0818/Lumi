// Electron 主进程：拉起 lumi serve sidecar、创建窗口、经 IPC 把 ws 连接信息给 renderer。
const { app, BrowserWindow, dialog, ipcMain, protocol, session, shell, Notification } = require('electron')
const { spawn } = require('node:child_process')
const net = require('node:net')
const fs = require('node:fs')
const path = require('node:path')

// 自定义协议：让 renderer 安全引用本地文件（绕过 http origin 下的 file:// 限制），
// 用于 present_files 预览面板里 <img>/<iframe> 加载图片/PDF/HTML。必须在 app ready 前登记。
protocol.registerSchemesAsPrivileged([
  { scheme: 'lumi-file', privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, bypassCSP: true } },
])

// 仅 img/pdf/html 走 src 加载需正确 content-type；文本类经 fetch().text() 读取，类型不敏感。
const PREVIEW_MIME = {
  '.pdf': 'application/pdf', '.html': 'text/html', '.htm': 'text/html',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.webp': 'image/webp', '.bmp': 'image/bmp', '.svg': 'image/svg+xml',
}

// 协议层硬上限：防超大文件（如大图缩略）整块读进内存把主进程撑爆。
// 前端预览另有更低的 UI 阈值（50MB）；这里只是兜底防御。
const MAX_SERVE_BYTES = 128 * 1024 * 1024

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
    if (
      !url.startsWith('http://127.0.0.1:5173') &&
      !url.startsWith('file://') &&
      !url.startsWith('lumi-file://')
    ) {
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

// present_files 预览：用系统默认应用打开 / 在访达中显示该文件
ipcMain.handle('lumi:open-path', (_e, p) => shell.openPath(String(p)))
ipcMain.handle('lumi:reveal-path', (_e, p) => shell.showItemInFolder(String(p)))
// 预览打开时探测文件是否还在（被移动/改名/删除则 false）；渲染卡片时不调，零开销。
// 用异步 access：避免 existsSync 在离线网络盘上同步阻塞整个主进程。
ipcMain.handle('lumi:path-exists', async (_e, p) => {
  try {
    await fs.promises.access(String(p))
    return true
  } catch {
    return false
  }
})

// 原生目录选择器（切换工作目录用），取消返回 null
ipcMain.handle('lumi:pick-directory', async () => {
  const r = await dialog.showOpenDialog({ properties: ['openDirectory', 'createDirectory'] })
  return r.canceled ? null : r.filePaths[0]
})

// 通知点击：把窗口带回前台（还原最小化 + 跨平台聚焦）
function focusMainWindow() {
  const win = BrowserWindow.getAllWindows()[0]
  if (!win) return
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
}

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
  // 只放行 local-fonts：让 renderer 的 queryLocalFonts() 枚举本机字体（设置→界面字体）。
  // 其余权限（camera/mic/geolocation/clipboard 等）一律拒绝——本应用不需要。
  session.defaultSession.setPermissionRequestHandler((_wc, perm, cb) => cb(perm === 'local-fonts'))
  session.defaultSession.setPermissionCheckHandler((_wc, perm) => perm === 'local-fonts')
  // 本地文件协议：lumi-file:///<abs-path>（renderer 端各路径段 encodeURIComponent）
  protocol.handle('lumi-file', async (request) => {
    try {
      const filePath = decodeURIComponent(new URL(request.url).pathname)
      const st = await fs.promises.stat(filePath)
      if (st.size > MAX_SERVE_BYTES) return new Response('Too large', { status: 413 })
      const data = await fs.promises.readFile(filePath)
      const mime = PREVIEW_MIME[path.extname(filePath).toLowerCase()] || 'application/octet-stream'
      return new Response(data, { headers: { 'content-type': mime } })
    } catch {
      return new Response('Not found', { status: 404 })
    }
  })
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
  // macOS 关窗后应用驻留 Dock，sidecar 保持运行（activate 重建窗口后直接复用）；
  // 在这里杀 sidecar 的话，Dock 唤起的新窗口会对着死端口永久重连。
  // 其他平台关窗即退出，sidecar 由 before-quit 清理。
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', stopSidecar)

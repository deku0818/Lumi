// Electron 主进程：拉起 lumi serve sidecar、创建窗口、经 IPC 把 ws 连接信息给 renderer。
const { app, BrowserWindow, dialog, ipcMain, protocol, session, shell, Notification, Menu } = require('electron')
const { spawn } = require('node:child_process')
const net = require('node:net')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')

// 本地 sidecar 的访问令牌：每次启动随机生成，经 `lumi serve --token` 注入；
// 前端连接时在 ?token= 携带。本地与远程公网部署走同一套鉴权，无本地特例。
const LOCAL_TOKEN = crypto.randomBytes(24).toString('hex')

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
let sidecarFailed = false // 打包后未装本地后端时为 true：不再重启，前端退化为纯远程 client

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

// 打包后的后端命令：优先内嵌后端（extraResources 里的 PyInstaller 产物，随 app 分发），
// 无则退回 PATH 上的 `lumi`（用户经 uv tool install / pip 自装）。都没有也不算错——
// 本地后端不可用，用户可在「设置→连接」加远程机器。
function packagedBackend() {
  const bundled = path.join(
    process.resourcesPath,
    'lumi-backend',
    process.platform === 'win32' ? 'lumi-backend.exe' : 'lumi-backend'
  )
  return fs.existsSync(bundled) ? bundled : 'lumi'
}

// dev：源码 `uv run lumi serve`（cwd=仓库）；打包后：packagedBackend()。
function startSidecar(port) {
  const dev = !app.isPackaged
  const cmd = dev ? 'uv' : packagedBackend()
  const args = dev
    ? ['run', 'lumi', 'serve', '--port', String(port), '--token', LOCAL_TOKEN]
    : ['serve', '--port', String(port), '--token', LOCAL_TOKEN]
  // PYTHONUNBUFFERED：PyInstaller 产物 stdout 接管道时块缓冲，日志会滞留到进程退出才刷出
  const opts = { env: { ...process.env, PYTHONUNBUFFERED: '1' }, stdio: ['ignore', 'pipe', 'pipe'] }
  if (dev) opts.cwd = PROJECT_ROOT
  serveProc = spawn(cmd, args, opts)
  serveProc.stdout.on('data', (d) => process.stdout.write(`[lumi serve] ${d}`))
  serveProc.stderr.on('data', (d) => process.stderr.write(`[lumi serve] ${d}`))
  serveProc.on('error', (e) => {
    // 多为 ENOENT：未装本地后端。不崩、不重启，前端连本地会显示离线，用远程即可。
    console.warn(`[lumi serve] 本地后端启动失败（${e.code || e.message}）；可在设置→连接添加远程机器`)
    serveProc = null
    sidecarFailed = true
  })
  serveProc.on('exit', (code) => {
    console.log(`[lumi serve] 退出，code=${code}`)
    serveProc = null
    // 非主动停止（崩溃/被外部杀）时同端口自愈重启；但 spawn 失败（未装）不重启
    if (!stopping && !sidecarFailed) {
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

function windowFromEvent(event) {
  return BrowserWindow.fromWebContents(event.sender) || BrowserWindow.getFocusedWindow()
}

function sendWindowState(win) {
  if (!win || win.webContents.isDestroyed()) return
  win.webContents.send('lumi:window:maximized', win.isMaximized())
}

function sendMenuAction(win, action) {
  if (!win || win.webContents.isDestroyed()) return
  win.webContents.send('lumi:menu-action', action)
}

function installHiddenMenu(win) {
  const wc = win.webContents
  const menu = Menu.buildFromTemplate([
    {
      label: 'File',
      submenu: [
        { label: 'New Chat', accelerator: 'CommandOrControl+N', click: () => sendMenuAction(win, 'new-chat') },
        { label: 'Settings', accelerator: 'CommandOrControl+,', click: () => sendMenuAction(win, 'settings') },
        { type: 'separator' },
        { label: 'Close Window', accelerator: 'Alt+F4', click: () => win.close() },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { label: 'Reload', accelerator: 'CommandOrControl+R', click: () => wc.reload() },
        { label: 'Reset Zoom', accelerator: 'CommandOrControl+0', click: () => wc.setZoomLevel(0) },
        { label: 'Zoom In', accelerator: 'CommandOrControl+=', click: () => wc.setZoomLevel(Math.min(wc.getZoomLevel() + 0.5, 9)) },
        { label: 'Zoom Out', accelerator: 'CommandOrControl+-', click: () => wc.setZoomLevel(Math.max(wc.getZoomLevel() - 0.5, -8)) },
        { type: 'separator' },
        { label: 'Toggle Developer Tools', accelerator: 'CommandOrControl+Shift+I', click: () => (wc.isDevToolsOpened() ? wc.closeDevTools() : wc.openDevTools()) },
      ],
    },
    {
      label: 'Help',
      submenu: [
        { label: 'Project Home', click: () => shell.openExternal('https://github.com/BreezeQi/Lumi') },
      ],
    },
  ])
  Menu.setApplicationMenu(menu)
  win.setMenuBarVisibility(false)
}

function createWindow() {
  const isMac = process.platform === 'darwin'
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 680,
    minHeight: 480,
    backgroundColor: '#1a1a19',
    icon: APP_ICON,
    autoHideMenuBar: !isMac,
    ...(isMac
      ? {
          titleBarStyle: 'hiddenInset',
          trafficLightPosition: { x: 16, y: 16 },
        }
      : {
          frame: false,
        }),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  if (!isMac) installHiddenMenu(win)

  win.on('maximize', () => sendWindowState(win))
  win.on('unmaximize', () => sendWindowState(win))

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

// ── 多机后端注册表（~/Library/.../userData/backends.json）──
// 形状：{ active: 'local' | <remoteId>, remotes: [{id, name, url, token}] }
// 本地 sidecar 是隐式后端（id='local'），不入表；远程机器才持久化。
function backendsFile() {
  return path.join(app.getPath('userData'), 'backends.json')
}
function readBackends() {
  try {
    const d = JSON.parse(fs.readFileSync(backendsFile(), 'utf8'))
    return { active: d.active || 'local', remotes: Array.isArray(d.remotes) ? d.remotes : [] }
  } catch {
    return { active: 'local', remotes: [] }
  }
}
function writeBackends(d) {
  try {
    fs.writeFileSync(backendsFile(), JSON.stringify(d, null, 2))
  } catch (e) {
    console.error('[backends] 写入失败:', e)
  }
}
// 把后端 id 解析为带 token 的 WS 地址。local → 本地 sidecar；远程 → 表里的 url+token。
function connectionFor(id) {
  if (id && id !== 'local') {
    const r = readBackends().remotes.find((x) => x.id === id)
    if (r) {
      const sep = r.url.includes('?') ? '&' : '?'
      return { wsUrl: `${r.url}${sep}token=${encodeURIComponent(r.token || '')}` }
    }
  }
  return { wsUrl: `ws://127.0.0.1:${wsPort}/ws?token=${LOCAL_TOKEN}` }
}

// renderer 按 backendId 拿对应机器的 WS 地址（带 token）。省略 id 回退到 active（兼容）。
// 方案甲：前端为每台机器各开连接，不再"切换活动"，故按 id 取而非取单一 active。
ipcMain.handle('lumi:connection', (_e, id) => connectionFor(id || readBackends().active))
ipcMain.handle('lumi:backends:list', () => readBackends())
ipcMain.handle('lumi:backends:save', (_e, b) => {
  const d = readBackends()
  if (b.id) {
    const i = d.remotes.findIndex((x) => x.id === b.id)
    if (i >= 0) d.remotes[i] = { ...d.remotes[i], ...b }
  } else {
    d.remotes.push({ id: crypto.randomBytes(6).toString('hex'), name: b.name, url: b.url, token: b.token || '' })
  }
  writeBackends(d)
  return d
})
ipcMain.handle('lumi:backends:remove', (_e, id) => {
  const d = readBackends()
  d.remotes = d.remotes.filter((x) => x.id !== id)
  if (d.active === id) d.active = 'local'
  writeBackends(d)
  return d
})
ipcMain.handle('lumi:backends:setActive', (_e, id) => {
  const d = readBackends()
  d.active = id
  writeBackends(d)
  return { active: id }
})

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

ipcMain.handle('lumi:window:minimize', (event) => {
  windowFromEvent(event)?.minimize()
})
ipcMain.handle('lumi:window:toggle-maximize', (event) => {
  const win = windowFromEvent(event)
  if (!win) return false
  if (win.isMaximized()) win.unmaximize()
  else win.maximize()
  return win.isMaximized()
})
ipcMain.handle('lumi:window:close', (event) => {
  windowFromEvent(event)?.close()
})
ipcMain.handle('lumi:window:is-maximized', (event) => {
  return !!windowFromEvent(event)?.isMaximized()
})

ipcMain.handle('lumi:menu-command', (event, command) => {
  const wc = event.sender
  switch (String(command)) {
    case 'undo':
      wc.undo()
      break
    case 'redo':
      wc.redo()
      break
    case 'cut':
      wc.cut()
      break
    case 'copy':
      wc.copy()
      break
    case 'paste':
      wc.paste()
      break
    case 'select-all':
      wc.selectAll()
      break
    case 'reload':
      wc.reload()
      break
    case 'reset-zoom':
      wc.setZoomLevel(0)
      break
    case 'zoom-in':
      wc.setZoomLevel(Math.min(wc.getZoomLevel() + 0.5, 9))
      break
    case 'zoom-out':
      wc.setZoomLevel(Math.max(wc.getZoomLevel() - 0.5, -8))
      break
    case 'toggle-devtools':
      wc.isDevToolsOpened() ? wc.closeDevTools() : wc.openDevTools()
      break
    case 'open-repo':
      shell.openExternal('https://github.com/BreezeQi/Lumi')
      break
  }
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

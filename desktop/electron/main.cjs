// Electron 主进程：拉起 lumi serve sidecar、创建窗口、经 IPC 把 ws 连接信息给 renderer。
const { app, BrowserWindow, dialog, ipcMain, protocol, session, shell, Notification, Menu } = require('electron')
const { spawn, execFile } = require('node:child_process')
const { promisify } = require('node:util')
const execFileAsync = promisify(execFile)
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

// 从 Dock/Finder 启动时，进程只继承 launchd 的默认 PATH（/usr/bin:/bin:/usr/sbin:/sbin）——
// 中间没有 shell，~/.zshrc 里 nvm、~/.local/bin 的注入全都没机会跑。受害的不止后端自己
// （dev 的 `uv`、打包 fallback 的 `lumi`），还有它 shell out 调的外部命令：lark-cli 的
// shutil.which 会判定「未安装」，gh / rg 同理。故拉起 sidecar 前借一次登录 shell 取回真实
// PATH，与当前 PATH 合并——rc 是「从头求值」的结果，取不到终端启动时已激活的 venv /
// direnv 注入，直接替换会让原本能解析的命令消失。登录 shell 的值在前（Dock 启动时它才是
// 完整的那份），去重保序。
//
// 异步：rc 带 nvm/conda 初始化时这次求值常要 0.5~2s，同步做会连主进程一起冻住（窗口画得
// 出来但 IPC 全部排队）。缓存的是 Promise 而非结果，故 whenReady 一开始就能 kick off，
// 与 pickPort/建窗/Electron 自身启动重叠，实际等待基本归零。
let pathPromise = null
function sidecarPath() {
  if (pathPromise === null) {
    pathPromise = loginShellPath().then((shellPath) =>
      [...new Set([...shellPath.split(path.delimiter), ...(process.env.PATH || '').split(path.delimiter)])]
        .filter(Boolean)
        .join(path.delimiter)
    )
  }
  return pathPromise
}

async function loginShellPath() {
  // Windows 无 launchd 那套，GUI 进程本就继承系统 PATH，合并时自会带上
  if (process.platform === 'win32') return ''
  try {
    // -i 才会读 rc（nvm 通常写在 .zshrc 而非 .zprofile）；rc 里的欢迎语会混进 stdout，
    // 故打 marker 再挑行。stderr 一并丢弃：rc 的告警不该污染判断。
    const { stdout } = await execFileAsync(
      process.env.SHELL || '/bin/zsh',
      ['-ilc', 'echo __LUMI_PATH__$PATH'],
      { encoding: 'utf8', timeout: 5000 }
    )
    const hit = stdout.split('\n').find((l) => l.startsWith('__LUMI_PATH__'))
    return hit ? hit.slice('__LUMI_PATH__'.length).trim() : ''
  } catch {
    // shell 缺失 / rc 卡死超时：只用原 PATH，宁可少几条路径也不能拖住启动
    return ''
  }
}

// dev：源码 `uv run lumi serve`（cwd=仓库）；打包后：packagedBackend()。
async function startSidecar(port) {
  const dev = !app.isPackaged
  const cmd = dev ? 'uv' : packagedBackend()
  // --exit-with-parent + stdin 管道：本进程死亡（含崩溃/强杀）时 OS 关闭管道，
  // sidecar 读到 stdin EOF 自退——否则孤儿 sidecar 会与新实例抢同一 checkpoint
  // 数据库，会话读写悬挂表现为「会话打不开」
  const serveArgs = ['serve', '--port', String(port), '--token', LOCAL_TOKEN, '--exit-with-parent']
  const args = dev ? ['run', 'lumi', ...serveArgs] : serveArgs
  const resolvedPath = await sidecarPath()
  if (stopping) return // 等 PATH 期间用户已退出：别再拉起孤儿进程
  // PYTHONUNBUFFERED：PyInstaller 产物 stdout 接管道时块缓冲，日志会滞留到进程退出才刷出
  const opts = {
    env: { ...process.env, PATH: resolvedPath, PYTHONUNBUFFERED: '1' },
    stdio: ['pipe', 'pipe', 'pipe'],
  }
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

const REPO_URL = 'https://github.com/deku0818/Lumi'

// 标题栏「视图/帮助」菜单动作的单一实现——隐藏原生菜单与自定义标题栏的 IPC 共用同一套逻辑。
// 编辑类命令（撤销/剪切/粘贴等）不在此处：它们依赖聚焦的可编辑元素，只经隐藏原生菜单的 role 走键盘快捷键。
function runMenuCommand(wc, command) {
  switch (String(command)) {
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
      shell.openExternal(REPO_URL)
      break
  }
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
        { label: 'Reload', accelerator: 'CommandOrControl+R', click: () => runMenuCommand(wc, 'reload') },
        { label: 'Reset Zoom', accelerator: 'CommandOrControl+0', click: () => runMenuCommand(wc, 'reset-zoom') },
        { label: 'Zoom In', accelerator: 'CommandOrControl+=', click: () => runMenuCommand(wc, 'zoom-in') },
        { label: 'Zoom Out', accelerator: 'CommandOrControl+-', click: () => runMenuCommand(wc, 'zoom-out') },
        { type: 'separator' },
        { label: 'Toggle Developer Tools', accelerator: 'CommandOrControl+Shift+I', click: () => runMenuCommand(wc, 'toggle-devtools') },
      ],
    },
    {
      label: 'Help',
      submenu: [
        { label: 'Project Home', click: () => runMenuCommand(wc, 'open-repo') },
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
          // hidden / hiddenInset 视觉一致（红绿灯位置由下方显式坐标控制）。
          // 注意：标题栏高度带内 ~14px 的鼠标命中错位（electron#40874/#21632 家族）
          // 与本选项无关——真正的修复是前端 .titlebar-interactive 的合成层提升，
          // 换回 hiddenInset 也不会复发
          titleBarStyle: 'hidden',
          // 固定位置（学 macOS 天气不随侧栏展开/收起迁移）：展开时落在悬浮侧栏内部
          // （面板内缩 10px、避开圆角）。y 比按钮中心线（28）偏上取 20：原生灯珠的
          // 视觉重心低于几何中心，数学对齐反而显矮，按用户目感上提 2px
          trafficLightPosition: { x: 26, y: 20 },
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
  runMenuCommand(event.sender, command)
})

// 单实例锁：双开（Windows 双击两次 / mac open -n）会各拉一个 sidecar 抢同一
// checkpoint 数据库，读写悬挂表现为「会话打不开」。抢锁失败 = 已有实例在跑，
// 立即退出；已有实例收到 second-instance 事件把自己带回前台。
if (!app.requestSingleInstanceLock()) {
  app.exit(0)
}
app.on('second-instance', focusMainWindow)

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
  sidecarPath() // 尽早 kick off 登录 shell 求值，与下面几步重叠
  wsPort = await pickPort()
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
  startSidecar(wsPort)
})

app.on('window-all-closed', () => {
  // macOS 关窗后应用驻留 Dock，sidecar 保持运行（activate 重建窗口后直接复用）；
  // 在这里杀 sidecar 的话，Dock 唤起的新窗口会对着死端口永久重连。
  // 其他平台关窗即退出，sidecar 由 before-quit 清理。
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', stopSidecar)

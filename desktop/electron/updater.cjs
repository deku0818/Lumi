// 应用内自动更新（electron-updater + GitHub Releases）。
//
// Win/Linux：全自动——后台静默下载，就绪后由用户点「重启更新」装上。
// macOS：CI 未做代码签名（desktop-build.yml 的 CSC_IDENTITY_AUTO_DISCOVERY=false），
//   而 Squirrel.Mac 会校验新旧版本签名同源，未签名的包一定装不上。故 mac 只调
//   checkForUpdates() 比对版本——该阶段纯读 latest-mac.yml，不启动 Squirrel 代理
//   （见 MacUpdater 的签名路径只在 downloadUpdate 才走），下载交给系统浏览器。
//   拿到 Developer ID 证书并在 CI 签名后，MANUAL_DOWNLOAD 改 false 即全自动。
const { app, ipcMain, BrowserWindow, shell } = require('electron')
const { autoUpdater } = require('electron-updater')

const MANUAL_DOWNLOAD = process.platform === 'darwin'

// 启动后延迟首检（避开与 sidecar 启动抢资源），此后按固定周期轮询。
const FIRST_CHECK_DELAY = 15_000
const CHECK_INTERVAL = 6 * 60 * 60 * 1000

// 对 renderer 暴露的唯一状态。status 取值：
// idle 未检查 / checking 检查中 / latest 已是最新 / available 发现新版（mac 或下载前）
// / downloading 下载中 / ready 已就绪待重启 / error 失败
let state = { status: 'idle', manual: MANUAL_DOWNLOAD, current: app.getVersion() }

function broadcast() {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.webContents.isDestroyed()) win.webContents.send('lumi:update:state', state)
  }
}

// 每次状态迁移都先清掉上一次的 error：patch 式合并会把过期的错误信息带进成功状态，
// 让「已是最新版本」下面挂着一条上次网络失败的提示。
function setState(patch) {
  state = { ...state, error: undefined, ...patch }
  broadcast()
}

// repoUrl: mac 手动下载时打开 releases 页；beforeQuit: 装更新前清理 sidecar；
// onInstallFailed: 安装没能让进程退出时回滚 beforeQuit 的副作用
function setupUpdater({ repoUrl, beforeQuit, onInstallFailed }) {
  const releasesUrl = `${repoUrl}/releases`
  let installing = false
  autoUpdater.autoDownload = !MANUAL_DOWNLOAD
  autoUpdater.logger = { info: console.log, warn: console.warn, error: console.error, debug: () => {} }

  autoUpdater.on('checking-for-update', () => setState({ status: 'checking' }))
  autoUpdater.on('update-not-available', () => setState({ status: 'latest' }))
  autoUpdater.on('update-available', (info) =>
    setState({ status: MANUAL_DOWNLOAD ? 'available' : 'downloading', version: info.version, percent: 0 })
  )
  // 按整数百分比节流：progress 事件在 220MB 的下载里会打上千次，而 UI 只认整数百分比，
  // 不去重的话就是上千次全窗口 IPC 广播 + React 重渲染换 100 次真实变化。
  autoUpdater.on('download-progress', (p) => {
    const percent = Math.round(p.percent)
    if (percent !== state.percent) setState({ status: 'downloading', percent })
  })
  autoUpdater.on('update-downloaded', (info) => setState({ status: 'ready', version: info.version }))
  autoUpdater.on('error', (e) => {
    const error = String(e?.message || e)
    if (installing) {
      // 安装阶段失败：进程没退成，而 beforeQuit 已经把 sidecar 收走了 —— 必须原地复活，
      // 否则用户面对的是一个前端还在、所有会话都打不开的窗口。状态退回 ready：包仍在
      // 本地，「重启更新」入口要保留，错误原因经 error 字段呈现。
      installing = false
      onInstallFailed()
      setState({ status: 'ready', error })
      return
    }
    // 包已在本地时，检查/下载类错误一律不许冲掉 ready —— 否则用户白白丢失更新入口。
    if (state.status === 'ready') return
    setState({ status: 'error', error })
  })

  // 包已在本地（ready）或正在下载时一律不再检查：此时任何一次检查失败都会把状态冲成
  // error，用户就此丢失「重启更新」入口，而更新包其实就在手边。周期检查与手动检查共用
  // 这道守卫 —— 两处不一致正是它最初的漏洞。
  async function maybeCheck() {
    if (state.status === 'ready' || state.status === 'downloading') return
    await autoUpdater.checkForUpdates().catch(() => {})
  }

  ipcMain.handle('lumi:update:state', () => state)
  ipcMain.handle('lumi:update:check', async () => {
    await maybeCheck()
    return state
  })
  ipcMain.handle('lumi:update:install', () => {
    if (MANUAL_DOWNLOAD) return shell.openExternal(releasesUrl)
    if (state.status !== 'ready') return
    // sidecar 必须先收走：quitAndInstall 后新实例会立刻起来抢同一 checkpoint 数据库。
    // 失败有两条路：同步抛异常，或异步 emit error（见上面的 error 处理器），两条都要回滚。
    installing = true
    beforeQuit()
    try {
      autoUpdater.quitAndInstall()
    } catch (e) {
      installing = false
      onInstallFailed()
      setState({ status: 'ready', error: String(e?.message || e) })
    }
  })

  // dev 下 isUpdaterActive() 恒 false，checkForUpdates 只打一行日志就返回，无需另加判断
  setTimeout(maybeCheck, FIRST_CHECK_DELAY)
  setInterval(maybeCheck, CHECK_INTERVAL)
}

module.exports = { setupUpdater }

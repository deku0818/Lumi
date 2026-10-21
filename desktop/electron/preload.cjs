// 上下文隔离桥：只暴露受控的连接信息获取接口，不给 renderer 任何 Node 能力。
const { contextBridge, ipcRenderer, webUtils } = require('electron')

const remoteListeners = new Set()
ipcRenderer.on('lumi:remote:event', (_event, frame) => { for (const cb of remoteListeners) cb(frame) })

function subscribe(event, callback) {
  function listener(_event, value) {
    callback(value)
  }
  ipcRenderer.on(event, listener)
  return () => ipcRenderer.removeListener(event, listener)
}

contextBridge.exposeInMainWorld('lumi', {
  platform: process.platform,
  windowControls: {
    minimize: () => ipcRenderer.invoke('lumi:window:minimize'),
    toggleMaximize: () => ipcRenderer.invoke('lumi:window:toggle-maximize'),
    close: () => ipcRenderer.invoke('lumi:window:close'),
    isMaximized: () => ipcRenderer.invoke('lumi:window:is-maximized'),
    setTitleBarOverlay: (opts) => ipcRenderer.invoke('lumi:window:set-titlebar-overlay', opts),
    onMaximizedChange: (cb) => {
      const listener = (_e, maximized) => cb(!!maximized)
      ipcRenderer.on('lumi:window:maximized', listener)
      return () => ipcRenderer.removeListener('lumi:window:maximized', listener)
    },
  },
  menuCommand: (command) => ipcRenderer.invoke('lumi:menu-command', command),
  // 原生右键菜单在主进程构建，文案由 renderer 按当前语言翻好推过去
  setMenuLabels: (labels) => ipcRenderer.invoke('lumi:menu-labels', labels),
  onMenuAction: (cb) => {
    const listener = (_e, action) => cb(String(action))
    ipcRenderer.on('lumi:menu-action', listener)
    return () => ipcRenderer.removeListener('lumi:menu-action', listener)
  },
  getConnection: (backendId) => ipcRenderer.invoke('lumi:connection', backendId),
  // 多机后端注册表：本地 sidecar 恒在（id=local），远程机器可增删
  backends: {
    list: () => ipcRenderer.invoke('lumi:backends:list'),
    save: (b, credentials) => ipcRenderer.invoke('lumi:backends:save', b, credentials),
    remove: (id) => ipcRenderer.invoke('lumi:backends:remove', id),
  },
  ssh: {
    hosts: () => ipcRenderer.invoke('lumi:ssh:hosts'),
    resolve: (config) => ipcRenderer.invoke('lumi:ssh:resolve', config),
    connect: (id, credentials) => ipcRenderer.invoke('lumi:ssh:connect', id, credentials),
    disconnect: (id) => ipcRenderer.invoke('lumi:ssh:disconnect', id),
    statuses: () => ipcRenderer.invoke('lumi:ssh:statuses'),
    questions: () => ipcRenderer.invoke('lumi:ssh:questions'),
    answer: (id, answer) => ipcRenderer.invoke('lumi:ssh:answer', id, answer),
    pickKey: () => ipcRenderer.invoke('lumi:ssh:pick-key'),
    cleanKeys: (id) => ipcRenderer.invoke('lumi:ssh:clean-keys', id),
    onStatus: (cb) => subscribe('lumi:ssh:status', cb),
    onQuestion: (cb) => subscribe('lumi:ssh:question', cb),
    onQuestionClosed: (cb) => subscribe('lumi:ssh:question-closed', cb),
  },
  remote: {
    open: (machine, channel, options) => ipcRenderer.invoke('lumi:remote:open', machine, channel, options),
    send: (channel, frame) => ipcRenderer.invoke('lumi:remote:send', channel, frame),
    close: channel => ipcRenderer.invoke('lumi:remote:close', channel),
    upload: (machine, operation, value) => ipcRenderer.invoke('lumi:remote:upload', machine, operation, value),
    onEvent: cb => {
      remoteListeners.add(cb)
      return () => remoteListeners.delete(cb)
    },
  },
  // artifacts 预览：用系统应用打开 / 在访达中显示 / 探测文件是否还在 / 读本地文本
  openPath: (p) => ipcRenderer.invoke('lumi:open-path', p),
  openCodexThread: (id) => ipcRenderer.invoke('lumi:open-codex-thread', id),
  revealInFolder: (p) => ipcRenderer.invoke('lumi:reveal-path', p),
  pathExists: (p) => ipcRenderer.invoke('lumi:path-exists', p),
  readText: (p) => ipcRenderer.invoke('lumi:read-text', p),
  // Electron 33 起拿文件绝对路径的唯一途径（File.path 已移除）；拿不到返回空串
  getPathForFile: (file) => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  // 应用内更新：状态由主进程单向推送，renderer 只读 + 触发检查/安装
  update: {
    state: () => ipcRenderer.invoke('lumi:update:state'),
    check: () => ipcRenderer.invoke('lumi:update:check'),
    install: () => ipcRenderer.invoke('lumi:update:install'),
    onState: (cb) => {
      const listener = (_e, s) => cb(s)
      ipcRenderer.on('lumi:update:state', listener)
      return () => ipcRenderer.removeListener('lumi:update:state', listener)
    },
  },
  // 系统通知经主进程发（renderer 的 HTML5 Notification 在 macOS dev 下不可靠），
  // 点击时主进程自行聚焦窗口并回传 tag
  notify: (payload) => ipcRenderer.invoke('lumi:notify', payload),
  onNotifyClick: (cb) => {
    const handler = (_e, tag) => cb(tag)
    ipcRenderer.on('lumi:notify-click', handler)
    return () => ipcRenderer.removeListener('lumi:notify-click', handler)
  },
})

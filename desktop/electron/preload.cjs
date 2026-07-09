// 上下文隔离桥：只暴露受控的连接信息获取接口，不给 renderer 任何 Node 能力。
const { contextBridge, ipcRenderer, webUtils } = require('electron')

contextBridge.exposeInMainWorld('lumi', {
  platform: process.platform,
  windowControls: {
    minimize: () => ipcRenderer.invoke('lumi:window:minimize'),
    toggleMaximize: () => ipcRenderer.invoke('lumi:window:toggle-maximize'),
    close: () => ipcRenderer.invoke('lumi:window:close'),
    isMaximized: () => ipcRenderer.invoke('lumi:window:is-maximized'),
    onMaximizedChange: (cb) => {
      const listener = (_e, maximized) => cb(!!maximized)
      ipcRenderer.on('lumi:window:maximized', listener)
      return () => ipcRenderer.removeListener('lumi:window:maximized', listener)
    },
  },
  menuCommand: (command) => ipcRenderer.invoke('lumi:menu-command', command),
  onMenuAction: (cb) => {
    const listener = (_e, action) => cb(String(action))
    ipcRenderer.on('lumi:menu-action', listener)
    return () => ipcRenderer.removeListener('lumi:menu-action', listener)
  },
  getConnection: (backendId) => ipcRenderer.invoke('lumi:connection', backendId),
  // 多机后端注册表：本地 sidecar 恒在（id=local），远程机器可增删 + 切活动
  backends: {
    list: () => ipcRenderer.invoke('lumi:backends:list'),
    save: (b) => ipcRenderer.invoke('lumi:backends:save', b),
    remove: (id) => ipcRenderer.invoke('lumi:backends:remove', id),
    setActive: (id) => ipcRenderer.invoke('lumi:backends:setActive', id),
  },
  // 原生目录选择器，用于切换工作目录；取消返回 null
  pickDirectory: () => ipcRenderer.invoke('lumi:pick-directory'),
  // present_files 预览：用系统应用打开 / 在访达中显示 / 探测文件是否还在
  openPath: (p) => ipcRenderer.invoke('lumi:open-path', p),
  revealInFolder: (p) => ipcRenderer.invoke('lumi:reveal-path', p),
  pathExists: (p) => ipcRenderer.invoke('lumi:path-exists', p),
  // Electron 33 起拿文件绝对路径的唯一途径（File.path 已移除）；拿不到返回空串
  getPathForFile: (file) => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  // 系统通知经主进程发（renderer 的 HTML5 Notification 在 macOS dev 下不可靠），
  // 点击时主进程自行聚焦窗口并回传 tag
  notify: (payload) => ipcRenderer.invoke('lumi:notify', payload),
  onNotifyClick: (cb) => ipcRenderer.on('lumi:notify-click', (_e, tag) => cb(tag)),
})

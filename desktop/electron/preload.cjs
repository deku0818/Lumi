// 上下文隔离桥：只暴露受控的连接信息获取接口，不给 renderer 任何 Node 能力。
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('lumi', {
  getConnection: () => ipcRenderer.invoke('lumi:connection'),
  // 通知点击时把窗口拉到前台（renderer 的 window.focus 在 macOS 不可靠）
  focusWindow: () => ipcRenderer.invoke('lumi:focus'),
  // 系统通知经主进程发（renderer 的 HTML5 Notification 在 macOS dev 下不可靠）
  notify: (payload) => ipcRenderer.invoke('lumi:notify', payload),
  onNotifyClick: (cb) => ipcRenderer.on('lumi:notify-click', (_e, tag) => cb(tag)),
  log: (msg) => ipcRenderer.send('lumi:log', msg),
})

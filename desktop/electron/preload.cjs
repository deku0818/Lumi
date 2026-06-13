// 上下文隔离桥：只暴露受控的连接信息获取接口，不给 renderer 任何 Node 能力。
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('lumi', {
  getConnection: () => ipcRenderer.invoke('lumi:connection'),
  // 原生目录选择器，用于切换工作目录；取消返回 null
  pickDirectory: () => ipcRenderer.invoke('lumi:pick-directory'),
  // 系统通知经主进程发（renderer 的 HTML5 Notification 在 macOS dev 下不可靠），
  // 点击时主进程自行聚焦窗口并回传 tag
  notify: (payload) => ipcRenderer.invoke('lumi:notify', payload),
  onNotifyClick: (cb) => ipcRenderer.on('lumi:notify-click', (_e, tag) => cb(tag)),
})

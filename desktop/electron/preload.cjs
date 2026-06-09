// 上下文隔离桥：只暴露受控的连接信息获取接口，不给 renderer 任何 Node 能力。
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('lumi', {
  getConnection: () => ipcRenderer.invoke('lumi:connection'),
})

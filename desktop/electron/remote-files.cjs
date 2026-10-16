function createRemoteFileHandler(manager, configFor) {
  async function file(request) {
    try {
      const url = new URL(request.url)
      const parts = url.pathname.split('/')
      const machine = decodeURIComponent(parts[1])
      const filename = '/' + parts.slice(2).map(decodeURIComponent).join('/')
      if (url.hostname !== 'file' || !['GET', 'HEAD'].includes(request.method)) return new Response(null, { status: 400 })
      const config = configFor(machine)
      const info = await manager.request(config, { op: 'file_stat', path: filename })
      let start = 0, end = info.size - 1, status = 200
      const range = request.headers.get('range')
      if (range) {
        const match = /^bytes=(\d*)-(\d*)$/.exec(range)
        if (!match || (!match[1] && !match[2])) return new Response(null, { status: 416 })
        if (!match[1]) start = Math.max(0, info.size - Number(match[2]))
        else { start = Number(match[1]); end = match[2] ? Math.min(end, Number(match[2])) : end }
        if (start > end || start >= info.size) return new Response(null, { status: 416, headers: { 'Content-Range': 'bytes */' + info.size } })
        status = 206
      }
      const headers = { 'Content-Type': info.mime, 'Content-Length': String(end - start + 1), 'Accept-Ranges': 'bytes', 'Access-Control-Allow-Origin': '*' }
      if (status === 206) headers['Content-Range'] = 'bytes ' + start + '-' + end + '/' + info.size
      let cancelled = false, offset = start
      const stream = request.method === 'HEAD' ? null : new ReadableStream({
        async pull(controller) {
          if (cancelled || request.signal.aborted) { controller.close(); return }
          if (offset > end) { controller.close(); return }
          try {
            const result = await manager.request(config, { op: 'file_read', path: filename, offset, length: Math.min(1024 * 1024, end - offset + 1) })
            const bytes = Buffer.from(result.data, 'base64')
            if (!bytes.length) { controller.close(); return }
            offset += bytes.length; controller.enqueue(bytes)
          } catch (error) { controller.error(error) }
        },
        cancel() { cancelled = true },
      })
      return new Response(stream, { status, headers })
    } catch (error) { return new Response(null, { status: error.status || 503, headers: { 'Access-Control-Allow-Origin': '*' } }) }
  }
  return file
}

function registerRemoteFilePolicy(session, BrowserWindow) {
  session.webRequest.onBeforeRequest({ urls: ['lumi-remote://*/*'] }, (details, callback) => {
    const win = BrowserWindow.getAllWindows().find(item => item.webContents.id === details.webContentsId)
    callback({ cancel: !win || details.frame?.frameTreeNodeId !== win.webContents.mainFrame.frameTreeNodeId })
  })
}

module.exports = { createRemoteFileHandler, registerRemoteFilePolicy }

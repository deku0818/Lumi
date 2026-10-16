const MAX_FRAME = 16 * 1024 * 1024

class RemoteWire {
  constructor(child, { onFrame = () => {}, onClose = () => {} } = {}) {
    Object.assign(this, { child, onFrame, onClose })
    this.buffer = Buffer.alloc(0)
    this.pending = new Map()
    this.nextId = 1
    this.closed = false
    this.ready = new Promise((resolve, reject) => { this.hello = resolve; this.rejectHello = reject })
    this.timer = setTimeout(() => this.fail(new Error('SSH 数据通道握手超时。')), 300000)
    child.stdout.on('data', chunk => this.read(chunk))
    child.stdin.on('error', error => this.fail(error))
    child.on('error', error => this.fail(error))
    child.on('close', () => this.fail(new Error('SSH 数据通道已断开。')))
  }

  read(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk])
    try {
      while (this.buffer.length >= 4) {
        const length = this.buffer.readUInt32BE(0)
        if (!length || length > MAX_FRAME) throw Error('远端数据帧长度无效。')
        if (this.buffer.length < length + 4) return
        const frame = JSON.parse(this.buffer.subarray(4, length + 4))
        this.buffer = this.buffer.subarray(length + 4)
        if (frame.type === 'hello') {
          clearTimeout(this.timer)
          this.hello(frame)
        } else if (frame.type === 'frame') {
          this.onFrame(frame)
        } else if (this.pending.has(frame.id)) {
          const pending = this.pending.get(frame.id)
          this.pending.delete(frame.id)
          clearTimeout(pending.timer)
          if (frame.error) {
            pending.reject(Object.assign(new Error(frame.error.message), { status: frame.error.status }))
          } else {
            pending.resolve(frame.result)
          }
        }
      }
    } catch (error) { this.fail(error) }
  }

  send(frame) {
    if (this.closed) return Promise.reject(Error('SSH 数据通道已断开。'))
    const data = Buffer.from(JSON.stringify(frame))
    if (data.length > MAX_FRAME) return Promise.reject(Error('远端数据帧过大。'))
    const prefix = Buffer.alloc(4)
    prefix.writeUInt32BE(data.length)
    return new Promise((resolve, reject) => {
      this.child.stdin.write(Buffer.concat([prefix, data]), error => {
        if (error) reject(error)
        else resolve()
      })
    })
  }

  request(message) {
    if (this.closed) return Promise.reject(Error('SSH 数据通道已断开。'))
    const id = this.nextId++
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail(new Error('远端请求超时。')), 120000)
      this.pending.set(id, { resolve, reject, timer })
      this.send({ ...message, id }).catch(error => {
        const pending = this.pending.get(id)
        if (pending) {
          clearTimeout(pending.timer)
          this.pending.delete(id)
          reject(error)
        }
      })
    })
  }

  fail(error) {
    if (this.closed) return
    this.closed = true
    clearTimeout(this.timer)
    this.rejectHello(error)
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer)
      pending.reject(error)
    }
    this.pending.clear()
    this.child.kill()
    this.onClose(error)
  }
}
module.exports = { RemoteWire, MAX_FRAME }

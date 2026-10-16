const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { PassThrough } = require('node:stream')
const { RemoteWire, MAX_FRAME } = require('../electron/remote-wire.cjs')
function fixture() {
  const child = new EventEmitter()
  child.stdout = new PassThrough(); child.stdin = new PassThrough()
  child.kill = () => { child.killed = true }
  return child
}
function encode(frame) {
  const payload = Buffer.from(JSON.stringify(frame)), prefix = Buffer.alloc(4)
  prefix.writeUInt32BE(payload.length)
  return Buffer.concat([prefix, payload])
}
test('fragmented SSH frames preserve unicode and multiplex responses and events', async () => {
  const child=fixture(), events=[]
  const wire=new RemoteWire(child,{onFrame:frame=>events.push(frame)})
  const hello={type:'hello',transport:'ssh-stdio',protocol:1}
  const request=wire.request({op:'runtime'})
  const frame={type:'frame',channel:'first',frame:{params:{text:'你好 🌟'}}}
  const bytes=Buffer.concat([encode(hello),encode(frame),encode({id:1,result:{pid:42}})])
  for(const byte of bytes) child.stdout.write(Buffer.from([byte]))
  assert.deepEqual(await wire.ready,hello)
  assert.deepEqual(await request,{pid:42})
  assert.deepEqual(events,[frame])
  wire.fail(Error('done'))
})
test('disconnect rejects pending RPC once and does not leave request timers', async () => {
  const child=fixture(), wire=new RemoteWire(child)
  const ready=assert.rejects(wire.ready,/disconnected/)
  const request=assert.rejects(wire.request({op:'open'}),/disconnected/)
  wire.fail(Error('disconnected')); wire.fail(Error('again'))
  await Promise.all([ready,request])
  assert.equal(wire.pending.size,0)
  assert.equal(child.killed,true)
  await assert.rejects(wire.request({op:'runtime'}),/断开/)
})
test('malformed framing closes the byte stream before allocating an oversized frame', async () => {
  for(const size of [0,MAX_FRAME+1]) {
    const child=fixture(),wire=new RemoteWire(child)
    const failed=assert.rejects(wire.ready,/长度无效/)
    const bytes=Buffer.alloc(4);bytes.writeUInt32BE(size);child.stdout.write(bytes)
    await failed
    assert.equal(child.killed,true)
  }
})

test('closing a channel awaits its server acknowledgement without reconnecting the machine', async () => {
  const { SshManager } = require('../electron/ssh-manager.cjs')
  const child = fixture(), wire = new RemoteWire(child)
  const manager = new SshManager({ emit: () => {} })
  manager.connect = () => { throw Error('Closing must not reconnect') }
  manager.sessions.set('remote', { wire })
  child.stdout.write(encode({ type: 'hello' }))
  await wire.ready
  let closed = false
  const closing = manager.closeChannel('remote', 'chat').then(() => { closed = true })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(closed, false)
  assert.equal(wire.pending.size, 1)
  child.stdout.write(encode({ id: 1, result: {} }))
  await closing
  assert.equal(closed, true)
  wire.fail(Error('done'))
  await manager.closeChannel('remote', 'chat')
  await manager.closeChannel('absent', 'chat')
})

test('cancelled open clears its timeout without disrupting another channel', async t => {
  const timers = new Set()
  t.mock.method(global, 'setTimeout', (callback, delay) => {
    const timer = { callback, delay }
    timers.add(timer)
    return timer
  })
  t.mock.method(global, 'clearTimeout', timer => timers.delete(timer))
  const child = fixture(), events = []
  const wire = new RemoteWire(child, { onFrame: frame => events.push(frame) })
  child.stdout.write(encode({ type: 'hello' }))
  await wire.ready
  try {
    const opening = assert.rejects(wire.request({ op: 'open', channel: 'slow' }), /opening was cancelled/)
    const closing = wire.request({ op: 'close', channel: 'slow' })
    child.stdout.write(encode({ id: 1, error: { message: 'Remote session opening was cancelled', status: 400 } }))
    child.stdout.write(encode({ id: 2, result: {} }))
    await opening
    await closing
    assert.equal(wire.pending.size, 0)

    for (const timer of [...timers]) {
      if (timer.delay <= 120000) timer.callback()
    }
    assert.equal(timers.size, 0)
    assert.equal(wire.closed, false)
    assert.equal(child.killed, undefined)
    const frame = { type: 'frame', channel: 'other', frame: { id: 7, result: { alive: true } } }
    child.stdout.write(encode(frame))
    assert.deepEqual(events, [frame])
    const request = wire.request({ op: 'runtime' })
    child.stdout.write(encode({ id: 3, result: { transport: 'ssh-stdio' } }))
    assert.deepEqual(await request, { transport: 'ssh-stdio' })
  } finally {
    wire.fail(Error('done'))
  }
})

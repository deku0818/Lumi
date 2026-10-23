const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')

function tick() {
  return new Promise(resolve => setImmediate(resolve))
}

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

test('SSH send failure releases the channel before notifying reconnect, exactly once', async () => {
  const release = deferred()
  let closes = 0, disconnected = 0, unsubscribed = 0
  const { SshSocket } = loadRenderer('src/sshSocket.ts', {
    crypto: require('node:crypto'), queueMicrotask,
    window: { lumi: { remote: {
      onEvent: () => () => { unsubscribed++ }, open: async () => {},
      send: async () => { throw Error('write failed') },
      close: () => { closes++; return release.promise },
    } } },
  })
  const socket = new SshSocket('remote', {})
  socket.onclose = () => { disconnected++ }
  await tick()
  socket.send('{"id":1}')
  await tick()
  socket.close()
  assert.equal(socket.readyState, 3)
  assert.equal(closes, 1)
  assert.equal(unsubscribed, 1)
  assert.equal(disconnected, 0, 'Reconnect must wait for the server to detach')
  release.resolve()
  await tick()
  assert.equal(disconnected, 1)
})

test('closing an SSH socket before its opening microtask does not start a late session', async () => {
  let opens = 0, closes = 0
  const { SshSocket } = loadRenderer('src/sshSocket.ts', {
    crypto: require('node:crypto'), queueMicrotask,
    window: { lumi: { remote: {
      onEvent: () => () => {}, open: async () => { opens++ }, close: async () => { closes++ },
    } } },
  })
  const socket = new SshSocket('remote', {})
  socket.close(); socket.close()
  await tick()
  assert.equal(opens, 0)
  assert.equal(closes, 1)
})

for (const failure of ['failed', 'timeout']) {
  test(`initial chat ${failure} rejects creation and cleans up its connection`, async () => {
    const handshake = deferred()
    let gateway, closed = 0, unsubscribed = 0
    class Gateway {
      constructor() { gateway = this }
      onEvent(callback) { this.event = callback; return () => { this.event = null; unsubscribed++ } }
      onState(callback) { this.state = callback; return () => { this.state = null; unsubscribed++ } }
      connect() {}
      whenReady() { return handshake.promise }
      close() { closed++ }
    }
    const connections = {}
    const { openChatConnection } = loadRenderer('src/gatewayConnections.ts', {
      window: { lumi: { getConnection: async () => ({ kind: 'local', wsUrl: 'ws://fixture' }) } },
    })
    const context = {
      createGateway: () => new Gateway(),
      connsRef: { current: connections }, activeRef: { current: '' }, setConn: () => {},
    }
    const open = (thread, workspace, backend) => openChatConnection(thread, workspace, backend, context)
    const opening = open('thread', '/project', 'local')
    const rejected = assert.rejects(opening, new RegExp(failure))
    await tick()
    const key = Object.keys(connections)[0]
    assert.equal(connections[key], gateway)
    handshake.reject(Error(failure))
    await rejected
    assert.equal(closed, 1)
    assert.equal(unsubscribed, 2, 'A late ready event must not create a ghost session')
    assert.equal(connections[key], undefined)
  })
}

test('first-message upload failure returns the intact draft to the failure handler', async () => {
  const { dispatchDraft } = loadRenderer('src/messageDelivery.ts')
  const attachments = [{ kind: 'file', name: 'report.csv', path: '/local/report.csv', blob: new File(['data'], 'report.csv') }]
  const draft = { text: 'draft', attachments }
  const failed = deferred()
  let sent = false
  const gateway = {
    resolveAttachments: async () => { throw Error('upload failed') },
    sendMessage: async () => { sent = true },
  }
  dispatchDraft(gateway, draft, { remote: true, toolMode: 'default' }, failed.resolve)
  assert.equal((await failed.promise).message, 'upload failed')
  assert.equal(sent, false)
  assert.equal(draft.text, 'draft')
  assert.equal(draft.attachments, attachments)
})

test('large remote images use bounded upload chunks and leave only paths in the message', async () => {
  const { Gateway } = loadRenderer('src/gateway.ts', {
    Uint8Array, setTimeout, clearTimeout,
    window: { lumi: { remote: { upload: async (_backend, operation, value) => {
      if (operation === 'begin') return { upload: value.name }
      if (operation === 'chunk') { chunks.push(value.bytes); return {} }
      if (operation === 'finish') return { path: '/remote/' + value.upload }
      throw Error('Unexpected cancellation')
    } } } },
  })
  const chunks = [], messages = []
  const gateway = new Gateway({ kind: 'ssh', backend: 'remote' })
  gateway.sendMessage = async (...args) => messages.push(args)
  const images = [13, 4].map((size, index) => ({ kind: 'image', name: `image-${index}.png`,
    blob: new File([new Uint8Array(size * 1024 * 1024)], `image-${index}.png`, { type: 'image/png' }),
    dataUrl: 'data:image/png;base64,fixture',
  }))
  const { deliverDraft } = loadRenderer('src/messageDelivery.ts')
  await deliverDraft(gateway, { text: 'draft', attachments: images }, { remote: true, toolMode: 'default' })
  assert.equal(messages.length, 1)
  assert.equal(messages[0][0], 'draft')
  assert.deepEqual(Array.from(messages[0][2]), ['/remote/image-0.png', '/remote/image-1.png'])
  assert.equal(chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0), 17 * 1024 * 1024)
  assert.ok(chunks.every(chunk => chunk.byteLength <= 1024 * 1024))
})

test('local images keep their inline payload and do not upload to SSH', async () => {
  const images = [{ kind: 'image', name: 'local.png', blob: new File(['data'], 'local.png'), dataUrl: 'data:image/png;base64,ZGF0YQ==' }]
  const messages = []
  const { deliverDraft } = loadRenderer('src/messageDelivery.ts')
  await deliverDraft({
    resolveAttachments: async (refs, remote) => { assert.equal(refs.length, 0); assert.equal(remote, false); return [] },
    sendMessage: async (...args) => messages.push(args),
  }, { text: 'draft', attachments: images }, { remote: false, toolMode: 'default' })
  assert.equal(messages[0][0][1].source.data, 'ZGF0YQ==')
})

test('control connections share an opening and reuse a healthy gateway', async () => {
  const { getControlConnection } = loadRenderer('src/gatewayConnections.ts')
  const connections = {}, openings = {}, ready = deferred()
  let created = 0
  const create = () => { created++; return ready.promise }
  const reconnect = async () => { throw Error('Healthy connection must be reused') }
  const first = getControlConnection('remote', connections, openings, create, reconnect)
  assert.equal(first, getControlConnection('remote', connections, openings, create, reconnect))
  const gateway = { dead: false }
  connections.remote = gateway
  ready.resolve(gateway)
  assert.equal(await first, gateway)
  assert.equal(await getControlConnection('remote', connections, openings, create, reconnect), gateway)
  assert.equal(created, 1)
  assert.equal(openings.remote, undefined)
})

test('failed control openings can retry; stopped gateways reconnect before reuse', async () => {
  const { getControlConnection } = loadRenderer('src/gatewayConnections.ts')
  const connections = {}, openings = {}
  await assert.rejects(getControlConnection('remote', connections, openings, async () => { throw Error('offline') }, async () => {}), /offline/)
  assert.equal(openings.remote, undefined)
  const gateway = { dead: true }, ready = deferred()
  connections.remote = gateway
  let complete = false
  const opening = getControlConnection('remote', connections, openings, () => { throw Error('Must reuse gateway') }, () => ready.promise)
  void opening.then(() => { complete = true })
  await tick()
  assert.equal(complete, false)
  ready.resolve()
  assert.equal(await opening, gateway)
})

test('SSH ready after failed startup creates the missing control gateway and loads sessions', async () => {
  const { getControlConnection, restoreMachineConnections } = loadRenderer('src/gatewayConnections.ts')
  const connections = {}, openings = {}, ready = deferred()
  let sshReady = false, created = 0, machineState = 'failed', sessions = []
  const create = async () => {
    if (!sshReady) throw Error('SSH unavailable')
    created++
    await ready.promise
    const gateway = { state: 'open', dead: false, rebase: () => assert.fail('New gateway connects itself') }
    connections.remote = gateway
    machineState = 'open'
    sessions = ['restored session']
    return gateway
  }
  const open = id => getControlConnection(id, connections, openings, create, async () => assert.fail('No old gateway to reconnect'))
  await assert.rejects(open('remote'), /SSH unavailable/)
  assert.equal(connections.remote, undefined)

  sshReady = true
  const connection = { kind: 'ssh', backend: 'remote' }
  const first = restoreMachineConnections('remote', connection, connections, {}, open)
  const second = restoreMachineConnections('remote', connection, connections, {}, open)
  assert.equal(created, 1, 'Repeated ready events share the pending control opening')
  ready.resolve()
  await Promise.all([first, second])
  assert.equal(machineState, 'open')
  assert.deepEqual(sessions, ['restored session'])
  await restoreMachineConnections('remote', connection, connections, {}, open)
  assert.equal(created, 1)
})

test('SSH restoration reconnects only closed channels on that machine and preserves live gateways', async () => {
  const { restoreMachineConnections } = loadRenderer('src/gatewayConnections.ts')
  const { sessionKey } = loadRenderer('src/lib/utils.ts')
  const restored = []
  const gateway = (id, state) => ({ state, rebase: connection => restored.push([id, connection.backend]) })
  const controls = { remote: gateway('control', 'failed'), other: gateway('other control', 'closed') }
  const chats = {
    [sessionKey('remote', 'closed')]: gateway('closed chat', 'closed'),
    [sessionKey('remote', 'live')]: gateway('live chat', 'open'),
    [sessionKey('other', 'closed')]: gateway('other chat', 'closed'),
  }
  const open = async () => assert.fail('Existing gateways must be reused')
  const connection = { kind: 'ssh', backend: 'remote' }
  await restoreMachineConnections('remote', connection, controls, chats, open)
  assert.deepEqual(restored, [['control', 'remote'], ['closed chat', 'remote']])
  controls.remote.state = 'open'
  await restoreMachineConnections('remote', connection, controls, {}, open)
  assert.equal(restored.length, 2)
})

test('chat reconnect preserves thread identity, live running state and event routing', async () => {
  const ready = deferred(), models = [], events = []
  let gateway, store = {}
  class ChatGateway {
    constructor() { gateway = this; this.bound = []; this.switched = [] }
    onEvent(callback) { this.event = callback; return () => { this.event = null } }
    onState() { return () => {} }
    connect() {}
    whenReady() { return ready.promise }
    bindThread(thread) { this.bound.push(thread) }
    async listBgTasks() { return { tasks: [] } }
    async switchSession(thread, workspace) { this.switched.push({ thread, workspace }); return { model: 'model' } }
    async loadHistory() { return { items: [] } }
  }
  const { openChatConnection } = loadRenderer('src/gatewayConnections.ts', {
    window: { lumi: { getConnection: async () => ({ kind: 'ssh', backend: 'remote' }) } },
  })
  const context = {
    createGateway: () => new ChatGateway(), connsRef: { current: {} }, activeRef: { current: '' },
    setStore: update => { store = update(store) }, setConn() {}, setWorkspaceDir() {},
    applySessionModel: (key, model) => models.push({ key, model }),
    handleEvent: (event, backend) => events.push({ event, backend }), onTasks() {},
  }
  const opening = openChatConnection(null, '/project', 'remote', context)
  await tick()
  gateway.event({ type: 'gateway.ready', session_id: 'first', payload: { workspace: '/project', workspace_bound: true, running: false } })
  ready.resolve()
  const key = await opening
  context.activeRef.current = key
  gateway.event({ type: 'gateway.ready', session_id: 'replacement', payload: { workspace: '/project', workspace_bound: true, running: true } })
  gateway.event({ type: 'approval.request', session_id: 'first', payload: { approval_id: 'pending' } })
  await tick()
  assert.deepEqual(gateway.switched, [{ thread: 'first', workspace: '/project' }])
  assert.equal(context.connsRef.current[key], gateway)
  assert.equal(store[key].running, true)
  assert.equal(models[0].key, key)
  assert.equal(events[0].backend, 'remote')
  assert.equal(events[0].event.payload.approval_id, 'pending')
})

test('SSH form fields inherit saved configuration without mixing in credentials or process state', () => {
  const { initialSshFields } = loadRenderer('src/lib/sshFormState.ts')
  assert.deepEqual({ ...initialSshFields() }, { host: '', name: '', user: '', port: '', identityFile: '', dataDir: '', auth: 'config' })
  const fields = initialSshFields({ id: 'remote', host: 'alias', name: 'GPU', port: 2222, identityFile: '/key', dataDir: '/data' })
  assert.equal(fields.auth, 'file')
  assert.equal(fields.port, '2222')
  assert.equal(fields.dataDir, '/data')
  assert.equal(initialSshFields({ identityFile: '/key', authMethod: 'password' }).auth, 'password')
  assert.equal('privateKey' in fields, false)
  assert.equal('busy' in fields, false)
})

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const React = require('react')
const { loadRenderer } = require('./renderer-module.cjs')

const tick = () => new Promise(resolve => setImmediate(resolve))
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

// Exercise App's navigation handlers and inspect the props sent to the real sidebar.
// Effects stay idle so the fixture controls gateway timing without background polling.
function appFixture() {
  const hooks = [], gateways = [], requests = []
  let index = 0
  const state = initial => {
    const slot = index++
    if (!(slot in hooks)) hooks[slot] = typeof initial === 'function' ? initial() : initial
    return [hooks[slot], value => { hooks[slot] = typeof value === 'function' ? value(hooks[slot]) : value }]
  }
  const memo = (factory, deps) => {
    const slot = index++, old = hooks[slot]
    if (!old || !deps || deps.some((value, i) => !Object.is(value, old.deps[i]))) hooks[slot] = { value: factory(), deps }
    return hooks[slot].value
  }
  const react = {
    ...React, useState: state,
    useReducer: (reduce, initial) => { const [value, set] = state(initial); return [value, action => set(old => reduce(old, action))] },
    useRef: initial => { const slot = index++; return hooks[slot] ?? (hooks[slot] = { current: initial }) },
    useMemo: memo, useCallback: (callback, deps) => memo(() => callback, deps),
    useEffect() {}, useLayoutEffect() {},
    useContext: () => ({ t: key => key, lang: 'zh', setLang() {} }),
  }
  class Gateway {
    constructor(connection, options) { this.backend = connection.backend; this.options = options; this.state = 'connecting'; gateways.push(this) }
    onEvent(callback) { this.event = callback; return () => {} }
    onState(callback) { this.changed = callback; return () => {} }
    connect() {
      this.setState('open')
      if (this.options && !Gateway.chatReady) this.event({ type: 'gateway.ready', session_id: 'thread', payload: { workspace: this.options.workspace } })
    }
    setState(value) { this.state = value; this.changed(value) }
    async whenReady() { if (this.options) await Gateway.chatReady }
    async listProjects() { return { projects: [{ path: this.backend === 'aws' ? '/work/Coo' : '/work/Lumi', name: this.backend === 'aws' ? 'Coo' : 'Lumi', default: true }] } }
    async listBgTasks() { return { tasks: [] } }
    async listSessions() { return { sessions: [] } }
    async setProvider(provider, model) {
      this.providerCalls = [...(this.providerCalls || []), { provider, model }]
      return { active: { provider, model } }
    }
    bindThread() {}
    close() {}
  }
  const gatewayPath = path.resolve(__dirname, '../src/gateway.ts')
  const { default: App } = loadRenderer('src/App.tsx', {
    process, Buffer, TextEncoder, TextDecoder, setImmediate, clearImmediate,
    require: name => name === 'react' ? react : name.endsWith('/src/gateway.ts') ? { Gateway } : require(name),
    localStorage: { getItem: () => null, removeItem() {} }, navigator: { language: 'zh' },
    window: { lumi: { platform: 'darwin', getConnection: async backend => { requests.push(backend); return { kind: 'ssh', backend } } } },
  }, ['react', 'react/*', 'react-dom', 'react-dom/*', gatewayPath])
  function render() {
    index = 0
    const nodes = []
    function visit(node) {
      if (!node || typeof node !== 'object') return
      nodes.push(node)
      for (const child of [node.props?.children].flat(Infinity)) visit(child)
    }
    visit(App())
    return {
      sidebar: nodes.find(node => node.props?.onNewChatIn).props,
      target: nodes.find(node => node.props?.ensureMachine && 'value' in node.props)?.props,
      project: nodes.find(node => node.props?.onOpen && node.props?.onSelectMachine)?.props,
      model: nodes.find(node => node.props?.onSwitchEffort)?.props,
      confirm: nodes.find(node => node.props?.onConfirm)?.props,
    }
  }
  return { render, gateways, requests, Gateway }
}

test('remote project plus then New Chat keeps the sidebar project, machine and connection together', async () => {
  const app = appFixture()
  await app.render().sidebar.onNewChatIn('aws', '/work/Coo')
  let page = app.render()
  assert.equal(page.sidebar.backend, 'aws')
  page.sidebar.onNew()
  page = app.render()
  assert.equal(page.sidebar.backend, 'aws', 'Clearing active must not fall back to local')
  assert.equal(page.sidebar.workspace, '', 'Do not show a stale project while resolving the new target')
  await tick()
  page = app.render()
  assert.equal(page.sidebar.backend, 'aws')
  assert.equal(page.sidebar.workspace, '/work/Coo')
  assert.equal(page.sidebar.conn, 'open')
  const control = app.gateways.find(gateway => !gateway.options)
  control.setState('closed')
  assert.equal(app.render().sidebar.conn, 'closed', 'Use the target control connection, not the old chat connection')
  app.render().sidebar.onNew()
  await tick()
  assert.equal(app.render().sidebar.backend, 'aws', 'Repeated New Chat must retain the remote machine')
  assert.ok(app.requests.every(backend => backend === 'aws'))
})

test('choosing a new-chat target and opening a project home override the old chat context', async () => {
  const app = appFixture()
  app.render().target.onChange({ backend: 'aws', path: '/work/Coo', name: 'Coo' })
  assert.equal(app.render().sidebar.backend, 'aws')
  assert.equal(app.render().sidebar.workspace, '/work/Coo')
  app.render().target.onChange({ backend: 'local', path: '/work/Lumi', name: 'Lumi' })
  assert.equal(app.render().sidebar.backend, 'local')
  assert.equal(app.render().sidebar.workspace, '/work/Lumi')
  app.render().sidebar.onOpenProjects()
  app.render().project.onSelectMachine('aws')
  app.render().project.onOpen('/work/Coo')
  assert.equal(app.render().sidebar.backend, 'aws')
  assert.equal(app.render().sidebar.workspace, '/work/Coo')
  app.render().sidebar.onNew()
  await tick()
  assert.equal(app.render().sidebar.backend, 'aws')
  assert.equal(app.render().sidebar.workspace, '/work/Coo')
})

test('new-chat model changes use the selected machine even after visiting another project home', async () => {
  const app = appFixture()
  await app.render().target.ensureMachine('local')
  const aws = await app.render().target.ensureMachine('aws')
  app.render().sidebar.onOpenProjects()
  app.render().project.onOpen('/work/Lumi')
  app.render().sidebar.onNew()
  await tick()
  app.render().target.onChange({ backend: 'aws', path: '/work/Coo', name: 'Coo' })
  app.render().model.onSwitch('remote-provider', 'remote-model')
  await tick()
  assert.deepEqual(aws.providerCalls, [{ provider: 'remote-provider', model: 'remote-model' }])
  assert.equal(app.gateways.find(gateway => gateway.backend === 'local').providerCalls, undefined)
  assert.equal(app.render().model.active.model, 'remote-model')
})

test('late model-change responses do not overwrite the newly selected machine model', async () => {
  const app = appFixture(), response = deferred()
  await app.render().target.ensureMachine('local')
  const aws = await app.render().target.ensureMachine('aws')
  aws.setProvider = () => response.promise
  app.render().target.onChange({ backend: 'aws', path: '/work/Coo', name: 'Coo' })
  app.render().model.onSwitch('remote-provider', 'remote-model')
  app.render().target.onChange({ backend: 'local', path: '/work/Lumi', name: 'Lumi' })
  app.render().model.onSwitch('local-provider', 'local-model')
  await tick()
  response.resolve({ active: { provider: 'remote-provider', model: 'remote-model' } })
  await tick()
  assert.equal(app.render().model.active.model, 'local-model')
})

for (const operation of ['refresh', 'rename', 'default', 'remove']) {
  test(`late local project ${operation} response stays on local after switching to AWS`, async () => {
    const app = appFixture(), response = deferred()
    const local = await app.render().target.ensureMachine('local')
    await app.render().target.ensureMachine('aws')
    app.render().sidebar.onOpenProjects()
    await tick()
    if (operation === 'refresh') {
      local.listProjects = () => response.promise
      app.render().sidebar.onOpenProjects()
    } else if (operation === 'rename') {
      local.renameProject = () => response.promise
      app.render().project.onRename('/work/Lumi', 'Renamed')
    } else if (operation === 'default') {
      local.setDefaultProject = () => response.promise
      app.render().project.onSetDefault('/work/Lumi', true)
    } else {
      local.removeProject = () => response.promise
      app.render().project.onRemove('/work/Lumi')
      // Removal has a confirmation dialog before the RPC.
      app.render().confirm.onConfirm()
    }
    app.render().project.onSelectMachine('aws')
    app.render().sidebar.onOpenProjects()
    await tick()
    assert.equal(app.render().project.projects[0].name, 'Coo')
    response.resolve({ projects: [{ path: '/work/Lumi', name: 'Local result' }] })
    await tick()
    assert.equal(app.render().project.machine, 'aws')
    assert.equal(app.render().project.projects[0].name, 'Coo')
    app.render().project.onSelectMachine('local')
    assert.equal(app.render().project.projects[0].name, 'Local result')
  })
}

for (const workspace of ['/srv/Coo', '']) {
  test(`failed AWS session activation ${workspace ? 'shows its own project' : 'clears the previous project'}`, async () => {
    const app = appFixture(), ready = deferred()
    const aws = await app.render().target.ensureMachine('aws')
    aws.listSessions = async () => ({ sessions: [{ thread_id: 'remote-thread', workspace_dir: workspace }] })
    await app.render().sidebar.onNewChatIn('local', '/work/Lumi')
    await tick()
    app.Gateway.chatReady = ready.promise
    const opening = app.render().sidebar.onSelect('remote-thread', 'aws')
    assert.equal(app.render().sidebar.workspace, workspace)
    await tick()
    ready.reject(Error('offline'))
    await assert.rejects(opening, /offline/)
    assert.equal(app.render().sidebar.backend, 'aws')
    assert.equal(app.render().sidebar.workspace, workspace)
    assert.equal(app.render().sidebar.conn, 'failed')
  })
}

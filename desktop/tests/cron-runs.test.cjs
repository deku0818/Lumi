const test = require('node:test')
const assert = require('node:assert/strict')
const React = require('react')
const { loadRenderer } = require('./renderer-module.cjs')

const globals = { process, Buffer, TextEncoder, TextDecoder, localStorage: { getItem: () => 'zh' }, navigator: { language: 'zh' }, window: { lumi: {} } }
const external = ['react', 'react/*', 'react-dom', 'react-dom/*']
const { mergeCronRuns } = loadRenderer('src/hooks/useCronRuns.ts', globals, external)
const { renderRecords, renderPage } = loadRenderer('tests/fixtures/cron-runs.tsx', globals, external)
const live = { job_id: 'job-a', thread_id: 'cron-live', started_at: '2026-11-01T23:00:00+08:00' }
const done = { ...live, job_name: 'Task', status: 'success', finished_at: '2026-11-01T23:04:08+08:00', duration_ms: 248000, output_summary: 'Finished', error: '' }
const job = { id: 'job-a', name: 'Task', backend: 'local', schedule: { type: 'cron', value: '0 23 * * *' }, prompt: 'Do it', enabled: true, next_run: null }
const tick = () => new Promise(resolve => setImmediate(resolve))

test('a live run is observable before history arrives and is replaced by one completed record', () => {
  const pending = mergeCronRuns(null, [live])
  assert.equal(pending[0], live)
  assert.ok(renderRecords(pending).includes('运行中'))
  assert.ok(!renderRecords(pending).includes('暂无执行记录'))
  const completed = mergeCronRuns([done], [live])
  assert.equal(completed.length, 1)
  assert.equal(completed[0], done)
  assert.ok(!renderRecords(completed).includes('运行中'))
  assert.equal(mergeCronRuns(null, [{ ...live, thread_id: '' }]), null)
})

test('task detail consumes the selected machine and job snapshot while history is loading', () => {
  assert.equal((renderPage(job, { local: [live] }).match(/>运行中</g) || []).length, 2)
  for (const snapshots of [{ remote: [live] }, { local: [{ ...live, job_id: 'job-b' }] }]) {
    assert.equal((renderPage(job, snapshots).match(/>运行中</g) || []).length, 0)
  }
})

test('long failure, timeout and stopped details stay in a closed portal, outside navigation', () => {
  for (const status of ['failed', 'timeout', 'stopped']) {
    const html = renderRecords([{ ...done, status, error: 'LONG_ERROR_DETAILS\n'.repeat(100) }])
    assert.ok(!html.includes('LONG_ERROR_DETAILS'))
    assert.ok(html.includes('data-state="closed"'))
    assert.ok(html.includes('查看详情'))
    // A separate navigation button and details trigger; no extra row for the error text.
    assert.equal((html.match(/<button\b/g) || []).length, 2)
    assert.ok(!/<button\b[^>]*>(?:(?!<\/button>)[\s\S])*<button\b/.test(html))
  }
  const legacy = renderRecords([{ ...done, thread_id: '' }])
  assert.ok(legacy.includes('disabled=""'))
  assert.ok(legacy.includes('查看详情'), 'Pruned sessions retain access to their summary')
})

function find(node, predicate) {
  if (predicate(node)) return node
  for (const child of [node?.props?.children].flat(Infinity)) {
    if (child && typeof child === 'object') { const match = find(child, predicate); if (match) return match }
  }
}

test('both live and completed rows open their own execution thread', () => {
  const react = { ...React, useContext: () => ({ t: key => key, lang: 'zh' }) }
  const { RunRecords } = loadRenderer('src/components/CronRuns.tsx', { ...globals, require: name => name === 'react' ? react : require(name) }, external)
  for (const entry of [live, done]) {
    let picked
    const list = RunRecords({ entries: [entry], onPick: id => { picked = id } })
    const row = find(list, node => node?.props?.run === entry)
    const card = row.type(row.props)
    const navigation = find(card, node => node?.type === 'button')
    navigation.props.onClick()
    assert.equal(picked, entry.thread_id)
    assert.ok(!card.props.className.split(' ').includes('flex-col'))
    assert.ok(!navigation.props.className.split(' ').includes('flex-col'))
  }
})

function runsFixture() {
  const hooks = [], calls = []
  let index = 0, effects, connection = 'open'
  const react = {
    ...React,
    useContext: () => ({ conn: { local: connection, remote: connection }, err: {} }),
    useState(initial) {
      const slot = index++
      if (!(slot in hooks)) hooks[slot] = initial
      return [hooks[slot], value => { hooks[slot] = value }]
    },
    useEffect(callback, deps) {
      const slot = index++, previous = hooks[slot]
      if (!previous || deps.some((value, i) => value !== previous.deps[i])) {
        previous?.cleanup?.()
        const state = { deps }
        hooks[slot] = state
        effects.push(() => { state.cleanup = callback() })
      }
    },
  }
  const { useCronRuns } = loadRenderer('src/hooks/useCronRuns.ts', { ...globals, require: name => name === 'react' ? react : require(name) }, external)
  const api = () => ({ listCronRuns(jobId) { let resolve; const promise = new Promise(yes => { resolve = yes }); calls.push({ jobId, resolve }); return promise } })
  return {
    calls,
    connection(value) { connection = value },
    render(props = {}) {
      index = 0; effects = []
      const { machine = 'local', jobId = live.job_id, version = 0, machineRuns = [live], enabled = true } = props
      const entries = useCronRuns(api, machine, jobId, version, machineRuns, enabled)
      effects.forEach(effect => effect())
      return entries
    },
  }
}

test('switching jobs discards late history replies and never shows the previous job', async () => {
  const fixture = runsFixture()
  assert.equal(fixture.render()[0].thread_id, live.thread_id)
  assert.equal(fixture.render({ jobId: 'job-b', machineRuns: [] }), null)
  fixture.calls[0].resolve({ runs: [done] })
  await tick()
  assert.equal(fixture.render({ jobId: 'job-b', machineRuns: [] }), null)
  fixture.calls[1].resolve({ runs: [] })
  await tick()
  assert.equal(fixture.render({ jobId: 'job-b', machineRuns: [] }).length, 0)
})

test('reconnecting and reopening the rail reload history; a closed rail makes no requests', async () => {
  const fixture = runsFixture()
  fixture.render({ enabled: false })
  assert.equal(fixture.calls.length, 0)
  fixture.render()
  fixture.calls[0].resolve({ runs: [] })
  await tick()
  fixture.connection('closed'); fixture.render()
  fixture.connection('open'); fixture.render()
  assert.equal(fixture.calls.length, 2)
  fixture.calls[1].resolve({ runs: [done] })
  await tick()
  const records = fixture.render()
  assert.equal(records.length, 1)
  assert.equal(records[0].status, 'success')
})

test('cron completion reuses chat cleanup and reloads the persisted interrupted tool', async () => {
  const { emptySession, reduceEvent } = loadRenderer('src/sessionReducer.ts')
  const { sessionKey } = loadRenderer('src/lib/utils.ts')
  const { openChatConnection } = loadRenderer('src/gatewayConnections.ts', {
    window: { lumi: { getConnection: async () => ({ kind: 'local' }) } },
  })
  const key = sessionKey('local', live.thread_id)
  let store = { [key]: emptySession() }, onEvent, historyCalls = 0
  const gateway = {
    onEvent(callback) { onEvent = callback; return () => {} },
    onState() { return () => {} },
    connect() { onEvent({ type: 'gateway.ready', payload: { running: false } }) },
    async whenReady() {}, bindThread() {}, async switchSession() { return {} },
    async listBgTasks() { return { tasks: [] } },
    async loadHistory() {
      historyCalls++
      return { items: historyCalls === 1 ? [] : [
        { id: 'tool', kind: 'tool', tool_call_id: 'tool', name: 'bash', output: 'Interrupted' },
        { id: 'answer', kind: 'assistant', text: 'partial' },
      ], next_cursor: null }
    },
  }
  const context = {
    createGateway: () => gateway, connsRef: { current: {} }, activeRef: { current: key },
    setStore: update => { store = update(store) }, getSession: id => store[id],
    setConn() {}, setWorkspaceDir() {}, applySessionModel() {}, onTasks() {},
    handleEvent: event => { store = reduceEvent(store, key, event, {}) },
  }
  await openChatConnection(live.thread_id, '', 'local', context)
  for (const [type, payload] of [
    ['turn.start', {}],
    ['tool.start', { name: 'bash', tool_call_id: 'tool', args: {} }],
    ['message.delta', { text: 'partial' }],
  ]) onEvent({ type, session_id: live.thread_id, payload })
  assert.equal(store[key].running, true)
  onEvent({ type: 'turn.complete', session_id: live.thread_id, payload: {} })
  await tick()
  assert.equal(historyCalls, 2)
  assert.equal(store[key].running, false)
  assert.ok(store[key].items.filter(item => item.kind === 'tool').every(item => item.done))
  assert.ok(store[key].items.filter(item => item.kind === 'assistant').every(item => !item.streaming))
})

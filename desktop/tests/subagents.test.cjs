const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')
const { mergeSubagentTimeline, emptySubagentTimeline } = loadRenderer('src/lib/subagentTimeline.ts')
const { observeSubagent } = loadRenderer('src/lib/subagentObserver.ts')
const { codexThreadUrl } = require('../electron/codex-links.cjs')
const plain = value => JSON.parse(JSON.stringify(value))
const event = (seq, type, payload = {}, turn = 't1234567') => ({ seq, agent_id: 'a1234567', turn_id: turn, type, payload })

test('snapshot racing live deltas produces one canonical reply and keeps rounds distinct', () => {
  let state = mergeSubagentTimeline(emptySubagentTimeline, [event(4, 'message.delta', { message_id: 'reply', text: 'hello' })])
  state = mergeSubagentTimeline(state, [
    event(1, 'message.submitted', { text: 'first', ordinal: 1 }), event(2, 'turn.start'),
    event(3, 'message.start', { message_id: 'reply' }), event(4, 'message.delta', { message_id: 'reply', text: 'hello' }),
    event(5, 'message.complete', { message_id: 'reply', text: 'hello world' }),
    event(6, 'turn.complete', { status: 'completed', output: 'hello world' }),
    event(7, 'message.submitted', { text: 'second', ordinal: 2 }, 't7654321'),
  ])
  assert.equal(state.rounds.length, 2)
  assert.equal(state.rounds[0].prompt, 'first')
  assert.equal(state.rounds[0].items.length, 1)
  assert.equal(state.rounds[0].items[0].text, 'hello world')
  assert.equal(state.rounds[0].items[0].streaming, false)
  assert.equal(state.rounds[1].status, 'queued')
  assert.equal(mergeSubagentTimeline(state, [event(4, 'message.delta')]), state)
})

test('native Claude child output cannot enter the root assistant reply', () => {
  const state = mergeSubagentTimeline(emptySubagentTimeline, [
    event(1, 'native.event', { event: { type: 'message_start', message: { id: 'root' } } }),
    event(2, 'message.delta', { text: 'root ' }),
    event(3, 'native.event', { parent_tool_use_id: 'nested', event: { type: 'message_start', message: { id: 'child' } } }),
    event(4, 'message.delta', { parent_tool_use_id: 'nested', text: 'child' }),
    event(5, 'message.complete', { parent_tool_use_id: 'nested', message: { content: [{ text: 'child' }] } }),
    event(6, 'message.delta', { text: 'done' }),
    event(7, 'message.complete', { message: { content: [{ text: 'root done' }] } }),
  ])
  assert.deepEqual(plain(state.rounds[0].items.map(item => item.text)), ['root done', 'child'])
  assert.equal(Object.values(state.rounds[0].parents)[0], 'nested')
  assert.equal(state.events.length, 7)
})

test('turn completion does not duplicate a root reply positioned before nested output', () => {
  const state = mergeSubagentTimeline(emptySubagentTimeline, [
    event(1, 'native.event', { event: { type: 'message_start', message: { id: 'root' } } }),
    event(2, 'message.delta', { text: 'root ' }),
    event(3, 'native.event', { parent_tool_use_id: 'nested', event: { type: 'message_start', message: { id: 'child' } } }),
    event(4, 'message.complete', { parent_tool_use_id: 'nested', message: { content: [{ text: 'child' }] } }),
    event(5, 'message.complete', { message: { content: [{ text: 'root done' }] } }),
    event(6, 'turn.complete', { status: 'completed', output: 'root done' }),
  ])
  assert.deepEqual(plain(state.rounds[0].items.map(item => item.text)), ['root done', 'child'])
  assert.equal(mergeSubagentTimeline(state, state.events), state)
})

test('a nested reply cannot suppress a missing root final result with the same text', () => {
  const state = mergeSubagentTimeline(emptySubagentTimeline, [
    event(1, 'message.complete', { parent_tool_use_id: 'nested', message: { content: [{ text: 'answer' }] } }),
    event(2, 'turn.complete', { status: 'completed', output: 'answer' }),
  ])
  const round = state.rounds[0]
  assert.equal(round.items.length, 2)
  assert.equal(round.parents[round.items[0].id], 'nested')
  assert.equal(round.parents[round.items[1].id], undefined)
  assert.equal(round.items[1].text, 'answer')
})

test('native command output, errors, reasoning and terminal fallback remain observable', () => {
  const state = mergeSubagentTimeline(emptySubagentTimeline, [
    event(1, 'tool.start', { name: 'commandExecution', tool_call_id: 'cmd', native_item: { command: 'pwd', cwd: '/work' } }),
    event(2, 'native.event', { method: 'item/reasoning/summaryTextDelta', delta: 'checking' }),
    event(3, 'tool.complete', { name: 'commandExecution', tool_call_id: 'cmd', native_item: { aggregatedOutput: 'denied', exitCode: 1 } }),
    event(4, 'turn.complete', { status: 'blocked', output: 'summary', error: 'requires approval' }),
  ])
  const round = state.rounds[0]
  assert.equal(round.thinking, '')
  assert.equal(round.items[0].name, 'bash')
  assert.equal(round.items[0].output, 'denied')
  assert.equal(round.items[0].error, true)
  assert.equal(round.items[1].text, 'summary')
  assert.equal(round.status, 'blocked')
  assert.equal(round.error, 'requires approval')
})

test('thinking is current activity and clears when text, tools or turn completion arrives', () => {
  let state = emptySubagentTimeline
  const append = (seq, kind, payload) => {
    state = mergeSubagentTimeline(state, [event(seq, kind, payload)])
    return state.rounds[0]
  }
  append(1, 'turn.start')
  assert.equal(append(2, 'thinking.delta', { text: 'check ' }).thinking, 'check ')
  assert.equal(append(3, 'thinking.delta', { text: 'state' }).thinking, 'check state')
  let round = append(4, 'message.delta', { message_id: 'reply', text: 'Found it' })
  assert.equal(round.thinking, '')
  assert.equal(round.streaming, true)
  round = append(5, 'native.event', { method: 'item/reasoning/summaryTextDelta', delta: 'verify' })
  assert.equal(round.thinking, 'verify')
  assert.equal(round.streaming, false)
  round = append(6, 'tool.start', { name: 'Read', tool_call_id: 'read' })
  assert.equal(round.thinking, '')
  assert.equal(round.streaming, false)
  append(7, 'thinking.delta', { text: 'finishing' })
  round = append(8, 'turn.complete', { status: 'completed' })
  assert.equal(round.thinking, '')
  assert.equal(round.streaming, false)
  assert.equal(state.events.length, 8)
})

test('nested thinking and text cannot replace the observed instance activity', () => {
  const state = mergeSubagentTimeline(emptySubagentTimeline, [
    event(1, 'thinking.delta', { text: 'root thinking' }),
    event(2, 'thinking.delta', { text: 'nested thinking', parent_tool_use_id: 'nested' }),
    event(3, 'message.delta', { text: 'nested text', parent_tool_use_id: 'nested' }),
  ])
  assert.equal(state.rounds[0].thinking, 'root thinking')
  assert.equal(state.rounds[0].streaming, false)
})

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const tick = () => new Promise(resolve => setImmediate(resolve))
function client() {
  const listeners = new Set(), calls = [], removed = []
  return {
    state: 'open', calls, removed,
    onEvent(handler) { listeners.add(handler); return () => listeners.delete(handler) },
    emit(type, payload = {}) { for (const handler of listeners) handler({ type, payload }) },
    observeSubagent(id, cursor) { calls.push(['observe', id, cursor]); return Promise.resolve({ events: [], cursor }) },
    subagentHistory(id, cursor) { calls.push(['history', id, cursor]); return Promise.resolve({ events: [], cursor }) },
    unobserveSubagent(id) { removed.push(id); return Promise.resolve({ agent_id: id }) },
  }
}

test('reconnect backfills from confirmed history, even after newer live events arrived', async () => {
  const gateway = client(), pending = deferred(), updates = []
  gateway.observeSubagent = (id, cursor) => { gateway.calls.push(['observe', id, cursor]); return pending.promise }
  const observer = observeSubagent(gateway, 'a1234567', 0, update => updates.push(update))
  gateway.emit('subagent.event', { agent_id: 'a1234567', turn_id: 't1234567', seq: 30, event_type: 'message.delta', data: { text: 'late' } })
  pending.resolve({ events: [event(10, 'turn.start')], cursor: 10 })
  await tick()
  gateway.observeSubagent = (id, cursor) => { gateway.calls.push(['observe', id, cursor]); return Promise.resolve({ events: [event(20, 'message.delta', { text: 'missing' })], cursor: 20 }) }
  gateway.emit('gateway.ready')
  await tick()
  assert.deepEqual(gateway.calls.at(-1), ['observe', 'a1234567', 10])
  assert.ok(updates.some(update => update.events?.some(event => event.seq === 20)))
  observer.close()
  assert.deepEqual(gateway.removed, ['a1234567'])
})

test('closing an observer suppresses late snapshots and unrelated instance events', async () => {
  const gateway = client(), pending = deferred(), updates = []
  gateway.observeSubagent = () => pending.promise
  const observer = observeSubagent(gateway, 'a1234567', 0, update => updates.push(update))
  gateway.emit('subagent.event', { agent_id: 'another', turn_id: 'turn', seq: 1, event_type: 'message.delta', data: {} })
  observer.close()
  pending.resolve({ events: [event(1, 'message.delta')], cursor: 1 })
  await tick()
  assert.ok(updates.every(update => !update.events))
})

test('history pages can have global sequence gaps and keep paginating', async () => {
  const gateway = client(), updates = []
  gateway.observeSubagent = () => Promise.resolve({ events: Array.from({ length: 500 }, (_, i) => event((i + 1) * 2, 'native.event')), cursor: 1000 })
  gateway.subagentHistory = (id, cursor) => { gateway.calls.push(['history', id, cursor]); return Promise.resolve({ events: [event(1010, 'turn.complete')], cursor: 1010 }) }
  const observer = observeSubagent(gateway, 'a1234567', 0, update => updates.push(update))
  await tick()
  assert.deepEqual(gateway.calls[0], ['history', 'a1234567', 1000])
  assert.equal(updates.at(-1).loading, false)
  observer.close()
})

test('native opening accepts only a thread UUID, not arbitrary custom protocols', () => {
  assert.equal(codexThreadUrl('00000000-0000-4000-8000-000000000001'), 'codex://threads/00000000-0000-4000-8000-000000000001')
  for (const value of ['file:///secret', 'codex://threads/x', 'id?query=x', null]) assert.throws(() => codexThreadUrl(value))
})

test('a failed subscription is retried as a subscription, not merely a history fetch', async () => {
  const gateway = client(), updates = []
  let attempts = 0
  gateway.observeSubagent = (id, cursor) => {
    gateway.calls.push(['observe', id, cursor])
    return ++attempts === 1 ? Promise.reject(new Error('temporary disconnect')) : Promise.resolve({ events: [], cursor })
  }
  const observer = observeSubagent(gateway, 'a1234567', 10, update => updates.push(update))
  await tick()
  observer.refresh()
  await tick()
  assert.deepEqual(gateway.calls, [['observe', 'a1234567', 10], ['observe', 'a1234567', 10]])
  assert.ok(updates.some(update => update.error?.includes('temporary disconnect')))
  assert.equal(updates.at(-1).loading, false)
  observer.close()
})

test('terminal delivery during a pending snapshot schedules a final persisted catch-up', async () => {
  const gateway = client(), pending = deferred()
  gateway.observeSubagent = () => pending.promise
  gateway.subagentHistory = (id, cursor) => { gateway.calls.push(['history', id, cursor]); return Promise.resolve({ events: [event(30, 'turn.complete', { status: 'completed' })], cursor: 30 }) }
  const observer = observeSubagent(gateway, 'a1234567', 0, () => {})
  gateway.emit('subagent.event', { agent_id: 'a1234567', turn_id: 't1234567', seq: 30, event_type: 'turn.complete', data: { status: 'completed' } })
  pending.resolve({ events: [event(10, 'turn.start')], cursor: 10 })
  await tick()
  assert.deepEqual(gateway.calls, [['history', 'a1234567', 10]])
  observer.close()
})

test('subagent observations never become main conversation messages', () => {
  const { reduceEvent, emptySession } = loadRenderer('src/sessionReducer.ts')
  const store = { main: emptySession() }
  const result = reduceEvent(store, 'main', { type: 'subagent.event', payload: { agent_id: 'a1234567', turn_id: 't1234567', seq: 1, event_type: 'message.delta', data: { text: 'private subagent reply' } } }, { model: 'fake', provider: '' })
  assert.equal(result, store)
  assert.equal(store.main.items.length, 0)
})

test('task state changes backfill dropped lifecycle frames without polling tool progress', async () => {
  const gateway = client()
  const observer = observeSubagent(gateway, 'a1234567', 0, () => {})
  await tick()
  const task = { task_id: 't1234567', subagent_id: 'a1234567', status: 'completed', progress: { phase: 'running', tools_done: 1 } }
  gateway.emit('bg_tasks.update', { tasks: [task] })
  await tick()
  assert.equal(gateway.calls.filter(call => call[0] === 'history').length, 1)
  gateway.emit('bg_tasks.update', { tasks: [{ ...task, progress: { ...task.progress, tools_done: 2 } }] })
  await tick()
  assert.equal(gateway.calls.filter(call => call[0] === 'history').length, 1)
  observer.close()
})

test('terminal questions survive live delivery, history replay and text-only native questions', () => {
  const complete = event(3, 'turn.complete', { status: 'blocked', questions: [
    { id: 'format', question: '选择输出格式？', header: '格式', options: [{ label: 'CSV', description: '表格' }, { label: 'JSON', description: '结构化' }, { label: '', description: '输入内容' }], multiSelect: true },
    { id: 'name', question: '报告叫什么名字？' },
  ] })
  let state = mergeSubagentTimeline(emptySubagentTimeline, [complete])
  assert.equal(state.rounds[0].questions[0].options[1].label, 'JSON')
  assert.deepEqual(plain(state.rounds[0].questions[1].options), [])
  const questions = plain(state.rounds[0].questions)
  state = mergeSubagentTimeline(state, [event(1, 'message.submitted', { text: '导出报告' }), event(2, 'turn.start'), complete])
  assert.deepEqual(plain(state.rounds[0].questions), questions)
  assert.equal(state.events.length, 3)
  assert.equal(state.rounds[0].questions[0].multiSelect, true)
})

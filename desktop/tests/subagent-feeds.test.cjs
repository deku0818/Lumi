const test = require('node:test')
const assert = require('node:assert/strict')
const React = require('react')
const { loadRenderer } = require('./renderer-module.cjs')
const tick = () => new Promise(resolve => setImmediate(resolve))

// Drive the real hook's effects, cleanup and state updates with an in-memory Gateway.
function mount(gateway, initial = {}) {
  const slots = [], deps = [], cleanups = []
  let index = 0, effects = [], dirty = true, value
  let props = { owner: 'parent', parentId: 'parent', visible: true, enabled: true, ...initial }
  const react = {
    ...React,
    useRef(initial) { const i = index++; return slots[i] ?? (slots[i] = { current: initial }) },
    useState(initial) {
      const i = index++
      if (!(i in slots)) slots[i] = initial
      return [slots[i], next => { const updated = typeof next === 'function' ? next(slots[i]) : next; if (updated !== slots[i]) { slots[i] = updated; dirty = true } }]
    },
    useCallback: fn => fn,
    useEffect(fn, next) {
      const i = index++
      if (!deps[i] || next.some((v, j) => !Object.is(v, deps[i][j]))) {
        deps[i] = next
        effects.push(() => { cleanups[i]?.(); cleanups[i] = fn() })
      }
    },
  }
  const { useSubagents } = loadRenderer('src/hooks/useSubagents.ts', {
    require: name => name === 'react' ? react : require(name),
    window: { addEventListener() {}, removeEventListener() {} },
  }, ['react'])
  function render() {
    let turns = 0
    while (dirty) {
      assert.ok(++turns < 30, 'Effects must settle without an update loop')
      dirty = false; index = 0; effects = []
      value = useSubagents(gateway, props.owner, props.parentId, props.enabled, props.visible)
      effects.forEach(fn => fn())
    }
  }
  return {
    get value() { return value },
    async flush() { for (let i = 0; i < 4; i++) { render(); await tick() } render(); return value },
    async update(next) { props = { ...props, ...next }; dirty = true; return this.flush() },
    unmount() { cleanups.forEach(fn => fn?.()) },
  }
}
function client() {
  const listeners = new Set(), calls = [], removed = []
  const event = (seq, id, type, payload = {}) => ({ seq, agent_id: id, turn_id: `turn-${id}`, type, payload })
  const histories = {
    a: [event(1, 'a', 'message.submitted', { text: 'first task' }), event(2, 'a', 'turn.start'), event(3, 'a', 'thinking.delta', { text: 'alpha thought' })],
    b: [event(4, 'b', 'message.submitted', { text: 'second task' }), event(5, 'b', 'turn.start'), event(6, 'b', 'message.delta', { run_id: 'reply', text: 'beta reply' })],
  }
  return {
    state: 'open', calls, removed, listeners, histories,
    agents: ['a', 'b'].map(id => ({ agent_id: id, name: 'explore', title: `Task ${id}`, status: 'running', active_turn_id: `turn-${id}`, queued_count: 0 })),
    onEvent(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    emit(type, payload) { for (const fn of listeners) fn({ type, payload }) },
    async listSubagents() { return { agents: this.agents } },
    async observeSubagent(id, cursor) { calls.push([id, cursor]); return { events: histories[id].filter(e => e.seq > cursor), cursor: histories[id].at(-1).seq } },
    async subagentHistory(id, cursor) { return { events: histories[id].filter(e => e.seq > cursor), cursor: histories[id].at(-1).seq } },
    async unobserveSubagent(id) { removed.push(id) },
  }
}

test('same-name cards share one independently projected feed per visible instance', async () => {
  const gateway = client(), view = mount(gateway)
  try {
    const data = await view.flush()
    assert.deepEqual(gateway.calls, [['a', 0], ['b', 0]])
    assert.equal(data.details.a.rounds[0].thinking, 'alpha thought')
    assert.equal(data.details.b.rounds[0].items[0].text, 'beta reply')
    assert.equal(data.details.b.rounds[0].streaming, true)
    gateway.emit('subagent.event', { agent_id: 'a', turn_id: 'turn-a', seq: 7, event_type: 'thinking.delta', data: { text: ' updated' } })
    await view.flush()
    assert.equal(view.value.details.a.rounds[0].thinking, 'alpha thought updated')
    assert.equal(view.value.details.b.rounds[0].items[0].text, 'beta reply')
  } finally { view.unmount() }
  assert.equal(gateway.listeners.size, 0)
})

test('opening and expanding reuse the card feed; returning backfills the other instance', async () => {
  const gateway = client(), view = mount(gateway)
  try {
    await view.flush()
    view.value.select('a'); await view.flush()
    assert.equal(view.value.detail, view.value.details.a)
    assert.deepEqual(gateway.removed, ['b'])
    view.value.toggleExpanded(); await view.flush()
    await view.update({ visible: false })
    await view.update({ visible: true })
    assert.equal(gateway.calls.filter(([id]) => id === 'a').length, 1)
    gateway.histories.b.push({ seq: 8, agent_id: 'b', turn_id: 'turn-b', type: 'message.delta', payload: { run_id: 'reply', text: ' caught up' } })
    view.value.close(); await view.flush()
    assert.deepEqual(gateway.calls.at(-1), ['b', 6])
    assert.equal(view.value.details.b.rounds[0].items[0].text, 'beta reply caught up')
    await view.update({ visible: false })
    assert.deepEqual(gateway.removed, ['b', 'a', 'b'])
  } finally { view.unmount() }
})

test('changing the owning conversation closes its feeds and starts from its own history', async () => {
  const gateway = client(), view = mount(gateway)
  try {
    await view.flush()
    gateway.histories.a[0] = { ...gateway.histories.a[0], payload: { text: 'new owner task' } }
    await view.update({ owner: 'another', parentId: 'another' })
    assert.deepEqual(gateway.calls.slice(-2), [['a', 0], ['b', 0]])
    assert.equal(view.value.details.a.rounds[0].prompt, 'new owner task')
    assert.deepEqual(gateway.removed, ['a', 'b'])
  } finally { view.unmount() }
})

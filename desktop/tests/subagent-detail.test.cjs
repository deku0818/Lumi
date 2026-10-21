const test = require('node:test')
const assert = require('node:assert/strict')
const React = require('react')
const { loadRenderer } = require('./renderer-module.cjs')

// Run the actual component's layout effects with a viewport that clamps offsets like the DOM.
function viewport(position, options = {}) {
  const hooks = [], dependencies = []
  let index = 0, layout = [], effects = [], onScroll
  const useEffect = queue => (callback, deps) => {
    const slot = index++, old = dependencies[slot]
    if (!old || deps.some((value, i) => old[i] !== value)) {
      dependencies[slot] = deps
      queue.push(callback)
    }
  }
  const react = {
    ...React, useMemo: factory => factory(), useContext: () => ({ t: key => key }),
    useRef: initial => { const slot = index++; return hooks[slot] ?? (hooks[slot] = { current: initial }) },
    useState: initial => { const slot = index++; if (!(slot in hooks)) hooks[slot] = initial; return [hooks[slot], () => {}] },
    useLayoutEffect: (callback, deps) => useEffect(layout)(callback, deps),
    useEffect: (callback, deps) => useEffect(effects)(callback, deps),
  }
  const { SubagentDetail } = loadRenderer('src/components/Subagents.tsx', {
    process, Buffer, TextEncoder, TextDecoder, setImmediate, clearImmediate,
    require: name => name === 'react' ? react : require(name),
    localStorage: { getItem: () => 'zh' }, navigator: { language: 'zh' }, window: { lumi: {} },
  }, ['react', 'react/*', 'react-dom', 'react-dom/*'])
  const element = {
    scrollHeight: 200, clientHeight: 200, top: 0,
    get scrollTop() { return this.top },
    set scrollTop(value) { this.top = Math.min(Math.max(0, value), this.scrollHeight - this.clientHeight) },
  }
  return {
    element,
    scroll(top) { element.scrollTop = top; onScroll() },
    render(loading, rounds, error = '') {
      index = 0; layout = []; effects = []
      const tree = SubagentDetail({
        agent: { agent_id: 'agent', executor: 'codex', cwd: '/tmp', status: 'idle', last_turn_status: 'completed' },
        detail: { rounds, events: [], loading, error, nativeId: null }, position,
        expanded: true, remote: false, online: true,
        onBack() {}, onExpand() {}, onRetry() {}, onOpenFile() {}, ...options,
      })
      hooks[0].current = element
      onScroll = findNode(tree, node => node?.props?.ref === hooks[0]).props.onScroll
      layout.forEach(callback => callback()); effects.forEach(callback => callback())
      return tree
    },
  }
}

test('returning to an instance waits for complete history and preserves its reading offset', () => {
  const saved = { top: 300, pinned: false, folds: {} }, view = viewport(saved)
  view.render(false, [])
  view.scroll(0)
  assert.equal(saved.top, 300, 'Empty history must not overwrite the saved position')
  view.element.scrollHeight = 400
  view.render(true, [{ id: 'one' }])
  view.scroll(0)
  assert.equal(saved.top, 300, 'A partial history page is not ready for restoration')
  view.element.scrollHeight = 2000
  view.render(false, [{ id: 'one' }])
  assert.equal(view.element.scrollTop, 300)
  view.scroll(500)
  view.element.scrollHeight = 2500
  view.render(false, [{ id: 'one' }, { id: 'two' }])
  assert.equal(view.element.scrollTop, 500, 'Later streaming must not reset manual reading')
  assert.equal(saved.pinned, false)
})

test('an instance already loaded when changing view restores without another fetch', () => {
  const view = viewport({ top: 300, pinned: false, folds: { one: true } })
  view.element.scrollHeight = 2000
  view.render(false, [{ id: 'one' }])
  assert.equal(view.element.scrollTop, 300)
})

test('a pinned instance follows new content after restoration', () => {
  const saved = { top: 100, pinned: true, folds: {} }, view = viewport(saved)
  view.render(true, [])
  view.element.scrollHeight = 2000
  view.render(false, [{ id: 'one' }])
  assert.equal(view.element.scrollTop, 1800)
  view.element.scrollHeight = 2500
  view.render(false, [{ id: 'one' }, { id: 'two' }])
  assert.equal(view.element.scrollTop, 2300)
})

function findNode(node, predicate) {
  if (predicate(node)) return node
  for (const child of [node?.props?.children].flat(Infinity)) {
    if (child && typeof child === 'object') { const found = findNode(child, predicate); if (found) return found }
  }
}

for (const remote of [false, true]) {
  test(`file card forwards ${remote ? 'remote' : 'local'} preview actions through the detail view`, () => {
    const { mergeSubagentTimeline, emptySubagentTimeline } = loadRenderer('src/lib/subagentTimeline.ts')
    const file = { name: 'report.csv', path: '/tmp/report.csv', kind: 'sheet' }
    const timeline = mergeSubagentTimeline(emptySubagentTimeline, [{ seq: 1, agent_id: 'agent', turn_id: 'one', type: 'tool.complete', payload: { name: 'artifacts', tool_call_id: 'files', output: JSON.stringify([file]) } }])
    let opened
    const view = viewport({ top: 0, pinned: true, folds: {} }, { remote, onOpenFile: file => { opened = file } })
    const tree = view.render(false, timeline.rounds)
    const round = findNode(tree, node => !!node?.props?.round)
    const card = findNode(round.type.type(round.props), node => Array.isArray(node?.props?.files))
    assert.equal(card.props.remote, remote)
    card.props.onOpen(card.props.files[0])
    assert.deepEqual(JSON.parse(JSON.stringify(opened)), file)
  })
}

test('a failed history fetch cannot consume restoration before retry succeeds', () => {
  const view = viewport({ top: 300, pinned: false, folds: {} })
  view.render(true, [{ id: 'live-fragment' }])
  view.render(false, [{ id: 'live-fragment' }], 'disconnected')
  assert.equal(view.element.scrollTop, 0)
  view.element.scrollHeight = 2000
  view.render(false, [{ id: 'full-history' }])
  assert.equal(view.element.scrollTop, 300)
})

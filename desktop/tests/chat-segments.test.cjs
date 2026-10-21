const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')
const { groupItems, segKey } = loadRenderer('src/lib/chatSegments.ts')
const { mergeSubagentTimeline, emptySubagentTimeline } = loadRenderer('src/lib/subagentTimeline.ts')
const plain = value => JSON.parse(JSON.stringify(value))
const tool = (id, patch = {}) => ({ id, kind: 'tool', toolCallId: id, name: 'bash', args: { command: 'pwd' }, output: '/work', done: true, ...patch })

test('consecutive tools share one group through running, failed and completed updates', () => {
  const first = tool('one', { historyGroup: 'page-anchor' })
  const second = tool('two')
  const running = tool('three', { done: false, output: '' })
  const initial = groupItems([first, second, running])
  assert.equal(initial.length, 1)
  assert.equal(initial[0].tools[0], first)
  assert.equal(initial[0].tools[2], running)
  const failed = tool('three', { error: true, output: 'permission denied' })
  const completed = groupItems([first, second, failed])
  assert.equal(completed.length, 1)
  assert.equal(completed[0].tools[2], failed)
  assert.equal(segKey(completed[0]), segKey(initial[0]))
  assert.equal(segKey(initial[0]), 'gpage-anchor')
})

test('assistant text and nested execution sources separate otherwise adjacent tools', () => {
  const text = { id: 'reply', kind: 'assistant', text: 'continuing', streaming: false }
  const items = [tool('a'), tool('b'), text, tool('c'), tool('d'), tool('e'), tool('f'), tool('g')]
  const parents = { d: 'child-one', e: 'child-one', f: 'child-two' }
  const groups = groupItems(items, parents)
  assert.deepEqual(plain(groups.map(group => group.kind === 'tools' ? group.tools.map(tool => tool.id) : group.item.id)), [
    ['a', 'b'], 'reply', ['c'], ['d', 'e'], ['f'], ['g'],
  ])
  assert.equal(items.length, 8)
  assert.equal(items[2], text)
})

test('main conversation keeps legacy agents and file cards separate from command groups', () => {
  const file = { path: '/report.html', name: 'report.html' }
  const groups = groupItems([
    tool('cmd'), tool('agent-one', { name: 'agent' }), tool('agent-two', { name: 'agent' }),
    tool('files', { name: 'artifacts', output: JSON.stringify([file]) }),
    tool('legacy-files', { name: 'present_files', output: 'invalid' }), tool('last'),
  ])
  assert.deepEqual(plain(groups.map(group => group.kind)), ['tools', 'agent', 'files', 'files', 'tools'])
  assert.equal(groups[1].items.length, 2)
  assert.deepEqual(plain(groups[2].files), [file])
  assert.deepEqual(plain(groups[3].files), [])
})

test('replayed subagent tools combine within each round without crossing turns', () => {
  const event = (seq, turn_id, type, payload) => ({ seq, agent_id: 'agent', turn_id, type, payload })
  const state = mergeSubagentTimeline(emptySubagentTimeline, [
    event(1, 'first', 'tool.start', { name: 'commandExecution', tool_call_id: 'one' }),
    event(2, 'first', 'tool.complete', { tool_call_id: 'one', output: 'ok' }),
    event(3, 'first', 'tool.complete', { name: 'Bash', tool_call_id: 'two', output: 'denied', is_error: true }),
    event(4, 'second', 'tool.start', { name: 'bash', tool_call_id: 'three' }),
  ])
  const groups = state.rounds.map(round => groupItems(round.items, round.parents))
  assert.deepEqual(plain(groups.map(segments => segments.map(segment => segment.tools.length))), [[2], [1]])
  assert.equal(groups[0][0].tools[1].error, true)
  assert.notEqual(segKey(groups[0][0]), segKey(groups[1][0]))
})

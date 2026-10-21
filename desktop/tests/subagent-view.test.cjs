const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')
const { mergeSubagentTimeline, emptySubagentTimeline } = loadRenderer('src/lib/subagentTimeline.ts')
const { renderDetail, renderClarify } = loadRenderer('tests/fixtures/subagent-detail.tsx', {
  process, Buffer, TextEncoder, TextDecoder, setImmediate, clearImmediate,
  localStorage: { getItem: () => 'zh' }, navigator: { language: 'zh' }, window: { lumi: {} },
})
const event = (seq, type, payload, turn_id) => ({ seq, type, payload, turn_id, agent_id: 'agent' })

const running = mergeSubagentTimeline(emptySubagentTimeline, [
  event(1, 'message.submitted', { text: 'RUNNING_SENTINEL' }, 'one'),
  event(2, 'turn.start', {}, 'one'),
])
const queued = [event(3, 'message.submitted', { text: 'QUEUED_SENTINEL' }, 'two')]

test('queuing another round leaves the running round expanded', () => {
  assert.ok(renderDetail(running).includes('RUNNING_SENTINEL'))
  const html = renderDetail(mergeSubagentTimeline(running, queued))
  assert.ok(html.includes('RUNNING_SENTINEL'))
  assert.ok(html.includes('QUEUED_SENTINEL'))
})

test('explicitly collapsed running rounds stay collapsed after another submission', () => {
  const html = renderDetail(mergeSubagentTimeline(running, queued), { one: false })
  assert.ok(!html.includes('RUNNING_SENTINEL'))
  assert.ok(html.includes('QUEUED_SENTINEL'))
})

test('older completed rounds still fold by default and respect explicit expansion', () => {
  const timeline = mergeSubagentTimeline(running, [
    event(3, 'turn.complete', { status: 'completed' }, 'one'),
    event(4, 'message.submitted', { text: 'QUEUED_SENTINEL' }, 'two'),
  ])
  assert.ok(!renderDetail(timeline).includes('RUNNING_SENTINEL'))
  assert.ok(renderDetail(timeline, { one: true }).includes('RUNNING_SENTINEL'))
})

test('blocked questions show their text and options read-only using the main question content', () => {
  const questions = [{ question: '导出格式？', header: '格式', options: [{ label: 'CSV', description: '表格文件' }, { label: 'JSON', description: '结构化文件' }, { label: '', description: 'CUSTOM_PLACEHOLDER' }], multiSelect: false }]
  const timeline = mergeSubagentTimeline(emptySubagentTimeline, [event(1, 'turn.complete', { status: 'blocked', questions }, 'one')])
  const html = renderDetail(timeline)
  for (const text of ['导出格式？', '格式', 'CSV', 'JSON', '表格文件', '结构化文件']) assert.ok(html.includes(text), text)
  assert.ok(!html.includes('CUSTOM_PLACEHOLDER'))
  assert.ok(!html.includes('<input'))
  const interactive = renderClarify(questions)
  for (const text of ['导出格式？', 'CSV', 'JSON', '表格文件', '结构化文件']) assert.ok(interactive.includes(text), text)
  assert.ok(interactive.includes('<input'), 'Main ask keeps its free-text input')
})

for (const remote of [false, true]) {
  test(`artifact results use the shared file card on ${remote ? 'remote' : 'local'} machines`, () => {
    const timeline = mergeSubagentTimeline(emptySubagentTimeline, [
      event(1, 'tool.complete', { name: 'artifacts', tool_call_id: 'files', output: JSON.stringify([{ name: 'report.csv', path: '/tmp/report.csv', kind: 'sheet', mime: 'text/csv' }]) }, 'one'),
      event(2, 'turn.complete', { status: 'completed' }, 'one'),
    ])
    const html = renderDetail(timeline, {}, remote)
    assert.ok(html.includes('report.csv'))
    assert.equal(html.includes('在访达中显示'), !remote)
    assert.ok(!html.includes('aria-expanded="false"'), 'Files are visible without opening a tool group')
  })
}

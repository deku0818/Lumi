const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')
const { mergeSubagentTimeline, emptySubagentTimeline } = loadRenderer('src/lib/subagentTimeline.ts')
const { renderDetail, renderClarify, renderAgents } = loadRenderer('tests/fixtures/subagent-detail.tsx', {
  process, Buffer, TextEncoder, TextDecoder, setImmediate, clearImmediate,
  localStorage: { getItem: () => 'zh' }, navigator: { language: 'zh' }, window: { lumi: {} },
})
const event = (seq, type, payload, turn_id) => ({ seq, type, payload, turn_id, agent_id: 'agent' })

const running = mergeSubagentTimeline(emptySubagentTimeline, [
  event(1, 'message.submitted', { text: 'RUNNING_SENTINEL' }, 'one'),
  event(2, 'turn.start', {}, 'one'),
])
const queued = [event(3, 'message.submitted', { text: 'QUEUED_SENTINEL' }, 'two')]

test('queuing another round keeps both task messages visible', () => {
  assert.ok(renderDetail(running).includes('RUNNING_SENTINEL'))
  const html = renderDetail(mergeSubagentTimeline(running, queued))
  assert.ok(html.includes('RUNNING_SENTINEL'))
  assert.ok(html.includes('QUEUED_SENTINEL'))
})

test('completed and queued rounds stay visible as one conversation', () => {
  const timeline = mergeSubagentTimeline(running, [
    event(3, 'turn.complete', { status: 'completed', output: 'COMPLETED_REPLY' }, 'one'),
    event(4, 'message.submitted', { text: 'QUEUED_SENTINEL' }, 'two'),
  ])
  const html = renderDetail(timeline)
  for (const text of ['RUNNING_SENTINEL', 'COMPLETED_REPLY', 'QUEUED_SENTINEL']) assert.ok(html.includes(text), text)
  assert.ok(!html.includes('Lumi →'), 'Tasks use regular user messages')
  assert.ok(!html.includes('aria-expanded='), 'Rounds and tasks do not have separate fold controls')
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
    const html = renderDetail(timeline, remote)
    assert.ok(html.includes('report.csv'))
    assert.equal(html.includes('在访达中显示'), !remote)
    assert.ok(!html.includes('aria-expanded="false"'), 'Files are visible without opening a tool group')
  })
}


test('same-name cards show caller titles and isolated real-time activity without technical IDs', () => {
  const agents = [
    { agent_id: 'ssh-private-id', name: 'explore', title: 'SSH 重连检查', status: 'running', active_turn_id: 'ssh', queued_count: 0 },
    { agent_id: 'render-private-id', name: 'explore', title: '消息流渲染检查', status: 'running', active_turn_id: 'render', queued_count: 0 },
  ]
  const timelines = {
    'ssh-private-id': mergeSubagentTimeline(emptySubagentTimeline, [event(1, 'turn.start', {}, 'ssh'), event(2, 'tool.start', { name: 'read', tool_call_id: 'reading' }, 'ssh')]),
    'render-private-id': mergeSubagentTimeline(emptySubagentTimeline, [event(3, 'turn.start', {}, 'render'), event(4, 'thinking.delta', { text: 'Verify the message pipeline' }, 'render')]),
  }
  const html = renderAgents(agents, timelines)
  for (const text of ['SSH 重连检查', '消息流渲染检查', '正在读取文件', '正在思考']) assert.ok(html.includes(text), text)
  assert.ok(html.indexOf('SSH 重连检查') < html.indexOf('正在读取文件'))
  assert.ok(html.indexOf('正在读取文件') < html.indexOf('消息流渲染检查'))
  assert.ok(!html.includes('ssh-private-id') && !html.includes('render-private-id'))
})

test('completed instances stay visible with their titles and no idle activity line', () => {
  const html = renderAgents(['SSH 重连检查', '消息流渲染检查'].map((title, i) => ({
    agent_id: `agent-${i}`, name: 'explore', title, status: 'idle', last_turn_status: 'completed', active_turn_id: null, queued_count: 0,
  })))
  assert.ok(html.includes('SSH 重连检查') && html.includes('消息流渲染检查'))
  assert.ok(html.includes('2 已完成'))
  assert.ok(!html.includes('空闲') && !html.includes('正在处理'))
})

test('stopped instances remain distinguishable from completed instances in a mixed list', () => {
  const agents = ['completed', 'cancelled', 'failed'].map((status, i) => ({
    agent_id: `agent-${i}`, name: 'explore', title: `任务 ${i}`, status: 'idle',
    last_turn_status: status, active_turn_id: null, queued_count: 0,
  }))
  const cards = renderAgents(agents).match(/<article\b[\s\S]*?<\/article>/g)
  assert.ok(!cards[0].includes('已停止') && !cards[0].includes('role="status"'))
  assert.match(cards[1], /role="status"[^>]*text-muted-foreground[^>]*>已停止</)
  assert.match(cards[2], /role="alert"[^>]*text-error[^>]*>失败</)
})

test('active instances show queued messages without duplicating the queue indication', () => {
  const agent = { agent_id: 'agent', name: 'explore', title: 'SSH 重连检查', status: 'running', active_turn_id: 'turn', queued_count: 3 }
  const running = renderAgents([agent])
  assert.ok(running.includes('正在处理'))
  assert.equal((running.match(/3 条排队/g) || []).length, 1)
  const queued = renderAgents([{ ...agent, status: 'queued' }])
  assert.equal((queued.match(/3 条排队/g) || []).length, 1)
  assert.ok(!renderAgents([{ ...agent, queued_count: 0 }]).includes('条排队'))
})

test('legacy instances without a caller title show only their name', () => {
  const html = renderAgents([{ agent_id: 'legacy-id', name: 'codex', title: '', status: 'idle', last_turn_status: 'completed', active_turn_id: null, queued_count: 0 }])
  assert.equal((html.match(/>codex</g) || []).length, 1)
  assert.ok(!html.includes('legacy-id'))
})

test('agent tool titles use the explicit title and keep full task arguments', () => {
  const { toolTitle, toolArgs } = loadRenderer('src/toolMeta.ts', { process })
  const t = key => key
  const args = { name: 'codex', title: 'SSH 重连检查', prompt: 'A much longer task with context and constraints' }
  assert.equal(toolTitle('agent_create', args, t), 'SSH 重连检查')
  assert.equal(toolArgs('agent_create', args, t).text, args.prompt)
  assert.equal(toolTitle('agent_create', { name: 'codex', prompt: 'Legacy task' }, t), 'Legacy task')
})

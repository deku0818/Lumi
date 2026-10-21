const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')
const { emptySession, reduceEvent, userBubble } = loadRenderer('src/sessionReducer.ts')

const defaultModel = { provider: 'test', model: 'test' }

for (const terminal of ['turn.complete', 'error']) {
  test(`automatic reply stays running until ${terminal} without anchoring a user or pinning the model`, () => {
    const user = userBubble('上一轮消息')
    let state = emptySession([user])
    const event = (type, payload = {}) => {
      state = reduceEvent({ chat: state }, 'chat', { type, payload }, defaultModel).chat
    }

    event('turn.start', { message_id: '' })
    assert.equal(state.running, true)
    assert.equal(typeof state.runStart, 'number')
    assert.equal(state.model, undefined)
    assert.equal(state.items[0], user)
    const runStart = state.runStart

    for (const [type, payload] of [
      ['thinking.delta', { text: '正在思考' }],
      ['message.start', {}],
      ['message.delta', { text: '后台结果续答' }],
      ['message.complete', { message_id: 'reply' }],
      ['tool.start', { tool_call_id: 'tool', name: 'bash' }],
      ['tool.complete', { tool_call_id: 'tool', output: 'ok' }],
    ]) {
      event(type, payload)
      assert.equal(state.running, true, type)
      assert.equal(state.runStart, runStart)
    }

    event(terminal, terminal === 'error' ? { message: 'provider failed' } : {})
    assert.equal(state.running, false)
    assert.equal(state.items.some(item => item.kind === 'assistant' && item.streaming), false)
    assert.equal(state.model, undefined)
    assert.equal(state.items[0], user)
  })
}

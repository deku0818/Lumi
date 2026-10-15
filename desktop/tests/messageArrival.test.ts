import assert from 'node:assert/strict'
import { test } from 'node:test'
import { addPending, emptySession, hydrateHistory, reduceEvent, userBubble } from '../src/sessionReducer.ts'

const model = { provider: 'test', model: 'test' }

test('server anchoring keeps the optimistic message entrance identity and original state intact', () => {
  const message = userBubble('新消息')
  const original = emptySession([message])
  const state = reduceEvent({ chat: original }, 'chat', {
    type: 'turn.start', payload: { message_id: 'server-message' },
  }, model).chat
  const anchored = state.items[0]
  assert.equal(anchored.kind, 'user')
  if (anchored.kind !== 'user') return
  assert.notEqual(anchored.id, message.id)
  assert.equal(anchored.arrival, message.arrival)
  assert.equal(anchored.messageId, 'server-message')
  assert.equal(original.items[0], message)
  assert.equal(message.messageId, undefined)
})
test('history hydration never restores entrance animations or streaming flags', () => {
  const state = hydrateHistory(emptySession(), { items: [
    { id: 'u', kind: 'user', text: '历史消息', message_id: 'history-message' },
    { id: 'a', kind: 'assistant', text: '历史回复' },
  ] })
  assert.equal(state.items[0].kind, 'user')
  assert.equal('arrival' in state.items[0], false)
  assert.equal(state.items[1].kind === 'assistant' && state.items[1].streaming, false)
})

test('the first assistant delta has an entrance identity that survives further streaming', () => {
  const first = reduceEvent({ chat: emptySession() }, 'chat', {
    type: 'message.delta', payload: { text: '第一段' },
  }, model)
  const second = reduceEvent(first, 'chat', {
    type: 'message.delta', payload: { text: '后续内容' },
  }, model)
  const a = first.chat.items[0]
  const b = second.chat.items[0]
  assert.ok(a.kind === 'assistant' && b.kind === 'assistant')
  assert.ok(a.arrival)
  assert.equal(a.arrival, b.arrival)
  assert.equal(b.text, '第一段后续内容')
})
test('pending interjection injection preserves its entrance identity and landing feedback', () => {
  const message = { ...userBubble('补充要求'), messageId: 'pending-message' }
  const pending = addPending(emptySession(), message)
  const state = reduceEvent({ chat: pending }, 'chat', {
    type: 'message.injected', payload: { message_ids: ['pending-message'] },
  }, model).chat
  assert.equal(state.pending.length, 0)
  const injected = state.items[0]
  assert.equal(injected.kind, 'user')
  if (injected.kind === 'user') {
    assert.equal(injected.arrival, message.arrival)
    assert.equal(injected.landed, true)
  }
})

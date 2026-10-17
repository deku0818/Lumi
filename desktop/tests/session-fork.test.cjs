const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')

const { emptySession, hydrateHistory, prependHistory, reduceEvent } = loadRenderer('src/sessionReducer.ts')
const point = { message_id: 'answer', checkpoint_id: 'checkpoint-before-future' }
const model = { provider: 'test', model: 'test' }
function event(state, type, payload = {}) {
  return reduceEvent({ chat: state }, 'chat', { type, payload }, model).chat
}

function plain(value) {
  return JSON.parse(JSON.stringify(value))
}

test('only the completed assistant gets the durable fork point, without changing earlier messages', () => {
  let state = emptySession([{id:'user', kind:'user', text:'question', messageId:'user'}])
  state = event(state, 'message.delta', {text:'answer'})
  assert.equal(state.items.at(-1).forkPoint, undefined)
  state = event(state, 'message.complete', {message_id:'answer'})
  assert.equal(state.items.at(-1).forkPoint, undefined, 'model completion alone is not a committed turn')
  const before = state
  state = event(state, 'turn.complete', {fork_point:point})
  assert.deepEqual(plain(state.items.at(-1).forkPoint), point)
  assert.equal(state.items[0], before.items[0])
  assert.equal(state.items[0].forkPoint, undefined)
  assert.equal(before.items.at(-1).forkPoint, undefined)
  assert.equal(state.running, false)
})

test('reloaded and archived replies preserve checkpoint anchors across history paging', () => {
  let state = hydrateHistory(emptySession(), {items:[
    {id:'current:user:0', kind:'user', text:'current question'},
    {id:'current:assistant', kind:'assistant', text:'current answer', fork_point:{message_id:'current',checkpoint_id:'latest'}},
  ],next_cursor:'older'})
  state = prependHistory(state, {items:[
    {id:'old:user:0', kind:'user', text:'old question'},
    {id:'answer:assistant', kind:'assistant', text:'old answer',fork_point:point},
  ],next_cursor:null}, 'older')
  assert.deepEqual(plain(state.items[1].forkPoint), point)
  assert.equal(state.items[0].messageId, undefined, 'archived user messages cannot rewind current context')
  assert.equal(state.items.at(-1).forkPoint.checkpoint_id, 'latest')
})

test('interrupted or failed output never acquires a fork point from the previous turn', () => {
  let state = hydrateHistory(emptySession(), {items:[
    {id:'answer:assistant', kind:'assistant', text:'previous answer',fork_point:point},
  ]})
  state = event(state, 'message.delta', {text:'interrupted'})
  state = event(state, 'error', {message:'connection lost'})
  assert.equal(state.items[1].forkPoint, undefined)
  state = event(state, 'turn.complete')
  assert.deepEqual(plain(state.items[0].forkPoint), point)
  assert.equal(state.items[1].forkPoint, undefined)
})

test('fork RPC preserves the source thread and passes the same retry identity over either transport', async () => {
  const { Gateway } = loadRenderer('src/gateway.ts')
  for (const connection of [{kind:'local',wsUrl:'ws://fixture'}, {kind:'ssh',backend:'remote'}]) {
    const gateway = new Gateway(connection)
    gateway.bindThread('source')
    const calls = []
    gateway.request = async (method, params) => {calls.push({method,params}); return {thread_id:'branch',title:'A · 分支 1',workspace_dir:'/project'}}
    const result = await gateway.forkSession('source',point.checkpoint_id,point.message_id,'request-id')
    await gateway.forkSession('source',point.checkpoint_id,point.message_id,'request-id')
    assert.equal(result.thread_id,'branch')
    assert.deepEqual(plain(calls), [0,1].map(()=>({method:'fork_session',params:{thread_id:'source',...point,request_id:'request-id'}})))
    assert.equal(gateway.options.thread,'source')
  }
})

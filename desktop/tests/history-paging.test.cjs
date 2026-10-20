const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')
const { emptySession, hydrateHistory, prependHistory, reduceEvent } = loadRenderer('src/sessionReducer.ts')

const plain = value => JSON.parse(JSON.stringify(value))
const user = (n, message = `m${n}`) => ({ id: `h${n}:user:0`, kind: 'user', text: `q${n}`, message_id: message })
const assistant = n => ({ id: `a${n}:assistant`, kind: 'assistant', text: `a${n}` })
const snapshot = (items, cursor = null, ids = {}) => ({ items, next_cursor: cursor, user_message_ids: ids })

test('stopping and reloading two live tools updates both rows without duplicating them', () => {
  const model = { model: 'fake', provider: '' }
  const event = (s, type, payload = {}) => reduceEvent({ session: s }, 'session', { type, payload }, model).session
  let state = emptySession()
  for (const id of ['one', 'two']) {
    state = event(state, 'tool.start', { name: 'bash', tool_call_id: id, args: { command: 'sleep 120' } })
  }
  state = event(state, 'tool.complete', { tool_call_id: 'two', output: 'Error: Timeout', is_error: true })
  state = event(state, 'turn.complete')
  state = hydrateHistory(state, snapshot(['one', 'two'].map(id => ({
    id: `tool:${id}`, kind: 'tool', name: 'bash', tool_call_id: id,
    output: id === 'two' ? 'Error: Timeout' : '', is_error: id === 'two',
  }))))
  assert.equal(state.items.length, 2)
  assert.ok(state.items.every(it => it.done))
  assert.equal(state.items[1].error, true)
  assert.equal(state.running, false)
})

test('prepend keeps chronological order, deduplicates boundaries and leaves live state intact', () => {
  let state = hydrateHistory(emptySession(), snapshot([user(3), assistant(3)], 'older', { 'h3:user:0': 'm3' }))
  state = { ...state, ctx: { sentinel: true }, pending: [user(9)], approval: [{ id: 'approval' }] }
  const next = prependHistory(state, snapshot([user(2, undefined), assistant(2), user(3)], 'oldest'), 'older')
  assert.deepEqual(plain(next.items.map(it => it.id)), ['h2:user:0', 'a2:assistant', 'h3:user:0', 'a3:assistant'])
  assert.equal(next.items[0].messageId, undefined, 'Archived users cannot target current context')
  assert.equal(next.items[2].messageId, 'm3')
  assert.equal(next.ctx, state.ctx)
  assert.equal(next.pending, state.pending)
  assert.equal(next.approval, state.approval)
  assert.equal(next.historyCursor, 'oldest')
})

test('reconnect refreshes latest page while retaining older pages and current user anchors', () => {
  let state = hydrateHistory(emptySession(), snapshot([user(2), assistant(2)], 'older'))
  state = prependHistory(state, snapshot([user(1), assistant(1)], null))
  state = hydrateHistory(state, snapshot([user(2, 'new-id'), assistant(2), user(3), assistant(3)], 'new-cursor', { 'h2:user:0': 'new-id', 'h3:user:0': 'm3' }))
  assert.deepEqual(plain(state.items.map(it => it.id)), ['h1:user:0','a1:assistant','h2:user:0','a2:assistant','h3:user:0','a3:assistant'])
  assert.equal(state.items[0].messageId, undefined)
  assert.equal(state.items[2].messageId, 'new-id')
  assert.equal(state.historyCursor, 'new-cursor')
})

test('a compact-only current checkpoint preserves already visible history until upward loading', () => {
  const state = hydrateHistory(emptySession(), snapshot([user(1), assistant(1)], null))
  const next = hydrateHistory(state, snapshot([], 'archive'))
  assert.equal(next.items.length, 2)
  assert.equal(next.items[0].messageId, undefined)
  assert.equal(next.historyCursor, 'archive')
})

test('streaming snapshot does not replace items or move the paging cursor', () => {
  const state = { ...emptySession([{ id: 1, kind: 'assistant', text: 'live', streaming: true }]), historyCursor: 'old-cursor' }
  const next = hydrateHistory(state, snapshot([assistant(1)], 'new-cursor'))
  assert.equal(next.items, state.items)
  assert.equal(next.historyCursor, 'old-cursor')
})

test('retry after prepending rolls back only the in-flight response', () => {
  let state = hydrateHistory(emptySession(), snapshot([user(2)], 'older'))
  const model = { model: 'fake', provider: '' }
  const event = (s, type, payload = {}) => reduceEvent({ session: s }, 'session', { type, payload }, model).session
  state = event(state, 'message.start')
  state = event(state, 'message.delta', { text: 'discard this response' })
  state = prependHistory(state, snapshot([user(1), assistant(1)], null))
  state = event(state, 'message.retry')
  assert.deepEqual(plain(state.items.slice(0, 3).map(it => it.id)), ['h1:user:0','a1:assistant','h2:user:0'])
  assert.equal(state.items.at(-1).kind, 'retry')
})

test('a stale page cannot restore deleted future messages after edit or reconnect', () => {
  const state = { ...emptySession(), historyCursor: 'new-branch' }
  assert.equal(prependHistory(state, snapshot([user(1)], null), 'old-branch'), state)
})

test('an initial history response cannot overwrite an edit that started while it was loading', async () => {
  let gateway, resolveHistory, store = {}
  const history = new Promise(resolve => { resolveHistory = resolve })
  class MockGateway {
    constructor() { gateway = this }
    onEvent(fn) { this.event = fn; return () => {} }
    onState() { return () => {} }
    connect() {}
    whenReady() { return Promise.resolve() }
    bindThread() {}
    async listBgTasks() { return {tasks:[]} }
    async switchSession() { return {} }
    loadHistory() { return history }
  }
  const {openChatConnection} = loadRenderer('src/gatewayConnections.ts', {window:{lumi:{getConnection:async()=>({kind:'local'})}}})
  const connsRef = {current:{}}
  const context = {createGateway:()=>new MockGateway(),connsRef,activeRef:{current:''},getSession:key=>store[key],setStore:fn=>{store=fn(store)},setConn(){},setWorkspaceDir(){},applySessionModel(){},handleEvent(){},onTasks(){}}
  const opening = openChatConnection('thread','/project','local',context)
  await new Promise(resolve=>setImmediate(resolve))
  gateway.event({type:'gateway.ready',payload:{running:false}})
  await new Promise(resolve=>setImmediate(resolve))
  const key = Object.keys(connsRef.current)[0]
  store[key] = {...store[key],historyRevision:1,historyCursor:null,running:true,items:[{id:'edited',kind:'user',text:'edited'}]}
  resolveHistory(snapshot([user(1),assistant(1)],'stale-cursor'))
  await opening
  assert.equal(store[key].items[0].id,'edited')
  assert.equal(store[key].historyCursor,null)
  assert.equal(store[key].running,true)
})

test('older tool rounds insert after the retained pending user and before the retained tool tail', () => {
  const state = hydrateHistory(emptySession(), snapshot([user(1), {id:'tool:tail',kind:'tool',tool_call_id:'tail',name:'bash',output:'tail result'}], 'archive', {'h1:user:0':'m1'}))
  const next = prependHistory(state, {items:[{id:'tool:older',kind:'tool',tool_call_id:'older',name:'bash',output:'older result'}],next_cursor:'earlier',insert_before:'tool:tail'})
  assert.deepEqual(plain(next.items.map(it=>it.id)), ['h1:user:0','tool:older','tool:tail'])
  assert.equal(next.items[1].historyGroup, 'tool:tail', 'Merging older tools preserves the expanded group')
  const previous = prependHistory(next, {items:[user(0),assistant(0)],next_cursor:null,insert_before:'h1:user:0'})
  assert.deepEqual(plain(previous.items.map(it=>it.id)), ['h0:user:0','a0:assistant','h1:user:0','tool:older','tool:tail'])
})

test('a default-sized page weaves earlier turns and tool rounds around a retained user', () => {
  const state = hydrateHistory(emptySession(), snapshot([user(1), {id:'tool:tail',kind:'tool',tool_call_id:'tail',name:'bash',output:'tail result'}], 'archive', {'h1:user:0':'m1'}))
  const older = {id:'tool:older',kind:'tool',tool_call_id:'older',name:'bash',output:'older result'}
  const page = {items:[user(0),assistant(0),older],next_cursor:null,order:['h0:user:0','a0:assistant','h1:user:0','tool:older'],insert_before:'tool:tail'}
  const next = prependHistory(state,page)
  assert.deepEqual(plain(next.items.map(it=>it.id)), ['h0:user:0','a0:assistant','h1:user:0','tool:older','tool:tail'])
  assert.deepEqual(plain(prependHistory(next,page).items.map(it=>it.id)), plain(next.items.map(it=>it.id)))
})

test('a page can span multiple retained interjections without moving their tool rounds', () => {
  const state = hydrateHistory(emptySession(), snapshot([user(1),user(2),{id:'tool:tail',kind:'tool',tool_call_id:'tail',name:'bash',output:'tail'}], 'archive'))
  const tool = n => ({id:`tool:${n}`,kind:'tool',tool_call_id:String(n),name:'bash',output:String(n)})
  const next = prependHistory(state,{items:[tool(0),tool(1)],next_cursor:null,order:['h1:user:0','tool:0','h2:user:0','tool:1'],insert_before:'tool:tail'})
  assert.deepEqual(plain(next.items.map(it=>it.id)), ['h1:user:0','tool:0','h2:user:0','tool:1','tool:tail'])
})

test('latest-page refresh retains archived records between shared pending users and tools', () => {
  const tool = n => ({id:`tool:${n}`,kind:'tool',tool_call_id:String(n),name:'bash',output:String(n)})
  const state = hydrateHistory(emptySession(),snapshot([user(0),assistant(0),user(1),tool(0),tool(1),tool(2)],'old'))
  const refreshed = hydrateHistory(state,snapshot([user(1),tool(1),tool(2),assistant(2)],'new',{'h1:user:0':'m1'}))
  assert.deepEqual(plain(refreshed.items.map(it=>it.id)), ['h0:user:0','a0:assistant','h1:user:0','tool:0','tool:1','tool:2','a2:assistant'])
})

test('explicit rewind replaces cached branch identities after restoring an older checkpoint', () => {
  const state = {...hydrateHistory(emptySession(),snapshot([user(1),assistant(1)],'old')),historyReset:true}
  const refreshed = hydrateHistory(state,snapshot([user(0),assistant(0),user(2),assistant(2)],'new'))
  assert.deepEqual(plain(refreshed.items.map(it=>it.id)), ['h0:user:0','a0:assistant','h2:user:0','a2:assistant'])
  assert.equal(refreshed.historyReset,false)
})

test('assistant completion gives live text the same identity as checkpoint history', () => {
  const model = { model: 'fake', provider: '' }
  let store = { session: emptySession() }
  store = reduceEvent(store, 'session', { type: 'message.delta', payload: { text: 'reply' } }, model)
  store = reduceEvent(store, 'session', { type: 'message.complete', payload: { message_id: 'answer' } }, model)
  assert.equal(store.session.items[0].id, 'answer:assistant')
  const next = hydrateHistory(store.session, snapshot([{ id:'answer:assistant', kind:'assistant', text:'reply' }], 'older'))
  assert.equal(next.items.length, 1)
})

test('latest-page refresh retains agent details and command receipts', () => {
  const tool = { id:'tool:tc', kind:'tool', toolCallId:'tc', name:'agent', output:'done', done:true, children:[{ text:'child details' }] }
  const state = emptySession([tool, { id:'command:receipt:assistant',kind:'assistant',text:'compacted',streaming:false }])
  const next = hydrateHistory(state, snapshot([{ id:'tool:tc', kind:'tool', tool_call_id:'tc', name:'agent', output:'done' }], 'older'))
  assert.deepEqual(plain(next.items[0].children), [{ text:'child details' }])
  assert.equal(next.items[1].text, 'compacted')
})

test('scroll restoration keeps the first visible message at its previous screen position', () => {
  const { captureHistoryScroll, restoreHistoryScroll } = loadRenderer('src/historyScroll.ts')
  let nodeTop = 110
  const node = { dataset:{ historyId:'anchor' }, getBoundingClientRect:() => ({ top:nodeTop,bottom:nodeTop+30 }) }
  const el = { scrollTop:80,scrollHeight:400,getBoundingClientRect:()=>({top:100}),querySelectorAll:()=>[node] }
  const anchor = captureHistoryScroll(el)
  nodeTop += 250; el.scrollHeight += 250
  restoreHistoryScroll(el, anchor)
  assert.equal(el.scrollTop, 330)
})

test('scroll restoration falls back to height delta when tool groups merge across the page boundary', () => {
  const { restoreHistoryScroll } = loadRenderer('src/historyScroll.ts')
  const el = { scrollTop:30,scrollHeight:900,querySelectorAll:()=>[] }
  restoreHistoryScroll(el, { id:'merged',top:0,scrollTop:30,height:400 })
  assert.equal(el.scrollTop, 530)
})

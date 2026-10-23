const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')

const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const tick = () => new Promise(resolve => setImmediate(resolve))
const waiting = { status: 'waiting', auth_id: 'auth-a', qr_code: 'data:image/png;base64,AA',
  verification_url: 'https://accounts.feishu.cn/verify', expires_in: 180 }

function fixture(gateway, onAuthorized = async () => {}) {
  const states = [], effects = []
  let index = 0
  const react = {
    useRef: value => ({ current: value }),
    useEffect: callback => effects.push(callback),
    useState: value => {
      const slot = index++
      states[slot] = value
      return [value, next => { states[slot] = next }]
    },
  }
  const { useMinutesAuth } = loadRenderer('src/components/useMinutesAuth.ts', {
    require: name => name === 'react' ? react : require(name),
  }, ['react'])
  const hook = useMinutesAuth(gateway, { app_id: 'app-a', cli_profile: 'bot-a' }, onAuthorized)
  return { hook, states, effects }
}

test('QR appears before waiting for authorization; successful authorization automatically runs diagnosis', async () => {
  const finish = deferred(), diagnose = deferred()
  let checked = 0
  const { hook, states } = fixture({
    startMinutesAuth: async () => waiting,
    finishMinutesAuth: () => finish.promise,
  }, () => { checked++; return diagnose.promise })
  const pending = hook.start()
  assert.equal(states[0].phase, 'preparing')
  await tick()
  assert.equal(states[0].phase, 'waiting')
  assert.equal(states[0].url, waiting.verification_url)
  assert.equal(checked, 0)
  finish.resolve({ status: 'authorized' })
  await tick()
  assert.equal(states[0].phase, 'checking')
  assert.equal(checked, 1)
  diagnose.resolve()
  await pending
  assert.equal(states[0].phase, 'idle')
})

test('cancelling during QR preparation discards and cancels the late authorization response', async () => {
  const start = deferred(), cancelled = []
  const { hook, states } = fixture({
    startMinutesAuth: () => start.promise,
    cancelMinutesAuth: async id => cancelled.push(id),
    finishMinutesAuth: () => { throw Error('Must not poll a discarded QR') },
  })
  const pending = hook.start()
  hook.cancel()
  start.resolve(waiting)
  await pending
  assert.equal(states[0].phase, 'idle')
  assert.deepEqual(cancelled, ['auth-a'])
})

test('closing the form cancels waiting and a late success cannot trigger diagnosis', async () => {
  const finish = deferred(), cancelled = []
  const { hook, effects } = fixture({
    startMinutesAuth: async () => waiting,
    finishMinutesAuth: () => finish.promise,
    cancelMinutesAuth: async id => cancelled.push(id),
  }, () => { throw Error('Closed form must not run diagnosis') })
  const cleanup = effects[0]()
  const pending = hook.start()
  await tick()
  cleanup()
  finish.resolve({ status: 'authorized' })
  await pending
  assert.deepEqual(cancelled, ['auth-a'])
})

test('expired authorization offers retry and never claims success', async () => {
  const { hook, states } = fixture({
    startMinutesAuth: async () => waiting,
    finishMinutesAuth: async () => ({ status: 'expired' }),
  }, () => { throw Error('Expired authorization must not run diagnosis') })
  await hook.start()
  assert.equal(states[0].phase, 'expired')
})

test('gateway errors are shown and pending authorization is cancelled', async () => {
  const cancelled = []
  const { hook, states } = fixture({
    startMinutesAuth: async () => waiting,
    finishMinutesAuth: async () => { throw { message: '连接已断开' } },
    cancelMinutesAuth: async id => cancelled.push(id),
  })
  await hook.start()
  assert.equal(states[0].phase, 'error')
  assert.equal(states[0].message, '连接已断开')
  assert.deepEqual(cancelled, ['auth-a'])
})

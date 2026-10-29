const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')
const { dreamTimePreview } = loadRenderer('src/lib/dreamSchedule.ts')

const tick = () => new Promise(resolve => setImmediate(resolve))
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function schedule(next_run_at = '2026-11-01T03:00:00+00:00', seconds = 3600) {
  return { next_run_at, timezone: 'UTC', refresh_after_seconds: seconds }
}
function watcher(gateway) {
  const window = new EventTarget(), document = new EventTarget(), timers = new Map()
  document.visibilityState = 'visible'
  let now = 0, nextTimer = 0, failures = 0
  const received = []
  const { watchDreamSchedule } = loadRenderer('src/lib/dreamSchedule.ts', {
    window, document, performance: { now: () => now },
    Date: class extends Date { static now() { throw Error('Client wall clock must not drive refresh') } },
    setTimeout: (callback, delay) => { const id = ++nextTimer; timers.set(id, { callback, delay }); return id },
    clearTimeout: id => timers.delete(id),
  })
  const stop = watchDreamSchedule(gateway, '03:00', value => {
    if (value === 'unavailable') failures++
    else received.push(value)
  })
  return {
    received, timers, stop, get failures() { return failures },
    elapse: milliseconds => { now += milliseconds },
    focus: () => window.dispatchEvent(new Event('focus')),
    visible: state => { document.visibilityState = state; document.dispatchEvent(new Event('visibilitychange')) },
    async expire() {
      const [id, timer] = timers.entries().next().value
      timers.delete(id); now += timer.delay
      await timer.callback()
    },
  }
}

test('03:00 UTC runs at 11:00 in Shanghai on the same day', () => {
  const result = dreamTimePreview('2026-10-29T03:00:00+00:00', 'Asia/Shanghai')
  assert.equal(result.serverTime, '03:00')
  assert.equal(result.serverOffset, 'UTC')
  assert.equal(result.clientTime, '11:00')
  assert.equal(result.clientOffset, 'UTC+8')
  assert.equal(result.dayOffset, 0)
  assert.equal(result.different, true)
})

test('conversions preserve both directions across calendar and year boundaries', () => {
  const next = dreamTimePreview('2026-12-31T19:00:00+00:00', 'Asia/Shanghai')
  assert.equal(next.clientTime, '03:00')
  assert.equal(next.dayOffset, 1)
  const previous = dreamTimePreview('2027-01-01T03:00:00+08:00', 'UTC')
  assert.equal(previous.clientTime, '19:00')
  assert.equal(previous.dayOffset, -1)
})

test('equal offsets need no conversion warning, including different zone names', () => {
  assert.equal(dreamTimePreview('2026-10-29T03:00:00+08:00', 'Asia/Shanghai').different, false)
  assert.equal(dreamTimePreview('2026-10-29T03:00:00+08:00', 'Asia/Singapore').different, false)
})

test('uses the execution date for client daylight saving time', () => {
  const before = dreamTimePreview('2026-11-01T05:30:00+00:00', 'America/New_York')
  const after = dreamTimePreview('2026-11-01T06:30:00+00:00', 'America/New_York')
  assert.equal(before.clientTime, '01:30')
  assert.equal(after.clientTime, '01:30')
  assert.equal(before.clientOffset, 'UTC−4')
  assert.equal(after.clientOffset, 'UTC−5')
})

test('handles fractional-hour offsets and midnight without 24:00', () => {
  const result = dreamTimePreview('2026-10-29T18:15:00+00:00', 'Asia/Kathmandu')
  assert.equal(result.clientTime, '00:00')
  assert.equal(result.clientOffset, 'UTC+5:45')
  assert.equal(result.dayOffset, 1)
  assert.equal(dreamTimePreview('2026-10-29T03:00:00+05:30', 'UTC').serverOffset, 'UTC+5:30')
})

test('extreme offsets can cross two calendar days', () => {
  const result = dreamTimePreview('2026-10-29T23:00:00-12:00', 'Pacific/Kiritimati')
  assert.equal(result.clientTime, '01:00')
  assert.equal(result.dayOffset, 2)
})

test('an expired preview fetches the next occurrence and updates the daylight saving conversion', async () => {
  const responses = [schedule(), schedule('2026-11-02T03:00:00+00:00')]
  const watch = watcher({ getDailyDreamSchedule: async () => responses.shift() })
  await tick()
  assert.equal(dreamTimePreview(watch.received[0].next_run_at, 'America/New_York').clientTime, '23:00')
  await watch.expire()
  assert.equal(dreamTimePreview(watch.received[1].next_run_at, 'America/New_York').clientTime, '22:00')
  assert.equal(watch.timers.size, 1)
  watch.stop()
})

test('refresh uses server remaining seconds, accounts for request time, and avoids the client wall clock', async () => {
  const response = deferred()
  const watch = watcher({ getDailyDreamSchedule: () => response.promise })
  watch.elapse(2500)
  response.resolve(schedule(undefined, 10))
  await tick()
  assert.equal([...watch.timers.values()][0].delay, 7500)
  watch.stop()
})

test('a response that arrives after its occurrence schedules a prompt refresh without a busy loop', async () => {
  const response = deferred()
  const watch = watcher({ getDailyDreamSchedule: () => response.promise })
  watch.elapse(2000)
  response.resolve(schedule(undefined, 1))
  await tick()
  assert.equal([...watch.timers.values()][0].delay, 1000)
  watch.stop()
})

test('focus and becoming visible refresh the preview; becoming hidden does not', async () => {
  let calls = 0
  const watch = watcher({ getDailyDreamSchedule: async () => { calls++; return schedule() } })
  await tick()
  watch.visible('hidden'); await tick()
  assert.equal(calls, 1)
  watch.visible('visible'); await tick()
  assert.equal(calls, 2)
  watch.focus(); await tick()
  assert.equal(calls, 3)
  assert.equal(watch.timers.size, 1)
  watch.stop()
  watch.focus(); watch.visible('visible'); await tick()
  assert.equal(calls, 3)
  assert.equal(watch.timers.size, 0)
})

test('a superseded response cannot overwrite a newer preview or its refresh timer', async () => {
  const first = deferred(), second = deferred()
  const responses = [first, second]
  const watch = watcher({ getDailyDreamSchedule: () => responses.shift().promise })
  watch.focus()
  second.resolve(schedule('2026-11-02T03:00:00+00:00', 7200))
  await tick()
  first.resolve(schedule())
  await tick()
  assert.equal(watch.received.length, 1)
  assert.equal(watch.received[0].next_run_at, '2026-11-02T03:00:00+00:00')
  assert.equal([...watch.timers.values()][0].delay, 7200000)
  watch.stop()
})

test('cleanup discards late results and failures without restoring timers', async () => {
  for (const fail of [false, true]) {
    const response = deferred()
    const watch = watcher({ getDailyDreamSchedule: () => response.promise })
    watch.stop()
    if (fail) response.reject(Error('closed'))
    else response.resolve(schedule())
    await tick()
    assert.equal(watch.received.length, 0)
    assert.equal(watch.failures, 0)
    assert.equal(watch.timers.size, 0)
  }
})

test('a failed refresh reports unavailable, then recovers on focus', async () => {
  let calls = 0
  const watch = watcher({ getDailyDreamSchedule: async () => {
    if (++calls === 1) throw Error('unreachable')
    return schedule()
  } })
  await tick()
  assert.equal(watch.failures, 1)
  assert.equal(watch.received.length, 0)
  watch.focus(); await tick()
  assert.equal(watch.received.length, 1)
  assert.equal(watch.timers.size, 1)
  watch.stop()
})

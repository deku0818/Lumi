const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')
const { watchSshStatuses } = loadRenderer('src/lib/sshStatus.ts')
const { renderSidebar, renderProgress } = loadRenderer('tests/fixtures/ssh-progress.tsx', {
  process, Buffer, TextEncoder, TextDecoder, setImmediate, clearImmediate,
  localStorage: { getItem: () => null }, navigator: { language: 'zh' }, window: { lumi: {} },
})
const status = (phase, extra = {}) => ({ id: 'remote', phase, message: '', ...extra })

test('live progress wins over an older startup snapshot, independently for each server', async () => {
  let receive, resolveSnapshot
  const seen = []
  const stop = watchSshStatuses({
    onStatus: fn => { receive = fn; return () => {} },
    statuses: () => new Promise(resolve => { resolveSnapshot = resolve }),
  }, value => seen.push(value))
  receive(status('restarting'))
  resolveSnapshot([status('updating'), status('ready', { id: 'other' })])
  await Promise.resolve()
  assert.deepEqual(seen.map(item => [item.id, item.phase]), [['remote', 'restarting'], ['other', 'ready']])
  receive(status('ready'))
  assert.equal(seen.at(-1).phase, 'ready')
  stop()
})

test('stopping observation unsubscribes and ignores a late snapshot without disconnecting SSH', async () => {
  let resolveSnapshot, unsubscribed = false, calls = 0
  const stop = watchSshStatuses({
    onStatus: () => () => { unsubscribed = true },
    statuses: () => new Promise(resolve => { resolveSnapshot = resolve }),
    connect: () => assert.fail('Viewing progress must not reconnect'),
    disconnect: () => assert.fail('Closing progress must not disconnect'),
  }, () => calls++)
  stop()
  resolveSnapshot([status('updating')])
  await Promise.resolve()
  assert.equal(unsubscribed, true)
  assert.equal(calls, 0)
})

test('a failed startup snapshot leaves the live subscription working', async () => {
  let receive
  const seen = []
  const stop = watchSshStatuses({
    onStatus: fn => { receive = fn; return () => {} },
    statuses: async () => { throw Error('snapshot unavailable') },
  }, value => seen.push(value))
  await Promise.resolve()
  receive(status('updating'))
  assert.equal(seen[0].phase, 'updating')
  stop()
})

test('updating servers show the Settings progress entry even with cached project sessions', () => {
  for (const sessions of [[], [{ thread_id: 'cached', title: '已有会话', backend: 'remote', workspace_dir: '/work', created_at: '2026-10-23T00:00:00Z' }]]) {
    const html = renderSidebar(status('updating'), sessions)
    assert.ok(html.includes('正在更新远端 Lumi'))
    assert.equal((html.match(/查看进度/g) || []).length, 1)
    assert.ok(!html.includes('连接中…'))
    if (sessions.length) assert.ok(html.includes('已有会话'))
  }
  assert.ok(!renderSidebar(status('ready')).includes('查看进度'))
  assert.ok(renderSidebar(status('error')).includes('连接未完成'))
})

test('the shared connection body follows preparation, reconnect, success and failure', () => {
  for (const [phase, title, step] of [
    ['updating', '正在更新远端 Lumi', '准备 Lumi'],
    ['restarting', '正在重启远端 Lumi', '准备 Lumi'],
    ['attaching', '正在建立 Lumi 连接', '建立连接'],
  ]) {
    const html = renderProgress(status(phase))
    assert.ok(html.includes(title))
    assert.ok(html.includes('aria-current="step"'))
    assert.ok(html.includes(step))
    assert.ok(!html.includes('ssh-host-summary'), 'Settings already shows the host row')
  }
  const ready = renderProgress(status('ready'))
  assert.ok(ready.includes('远端 Lumi 已就绪'))
  assert.ok(!ready.includes('aria-current="step"'))
  assert.ok(!ready.includes('class="lumi-orb '))
  const failed = renderProgress(status('error', { stage: 'updating', message: '下载连接中断' }))
  assert.ok(failed.includes('下载连接中断'))
  assert.ok(failed.includes('role="alert"'))
  assert.ok(!renderProgress(status('offline')).includes('class="lumi-orb lumi-orb-sm"'), 'Offline must not keep pulsing')
})

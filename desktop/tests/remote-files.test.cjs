const test = require('node:test')
const assert = require('node:assert/strict')
const { createRemoteFileHandler } = require('../electron/remote-files.cjs')

function fixture() {
  const data = Buffer.from('abcdefghi')
  const operations = []
  const config = { id: 'remote' }
  const handler = createRemoteFileHandler({ request: async (machine, operation) => {
    assert.equal(machine, config)
    operations.push(operation)
    if (operation.op === 'file_stat') return { size: data.length, mime: 'text/plain' }
    return { data: data.subarray(operation.offset, operation.offset + operation.length).toString('base64') }
  } }, id => {
    assert.equal(id, 'remote')
    return config
  })
  return { handler, operations }
}

for (const [range, expected, contentRange] of [
  [null, 'abcdefghi', null], ['bytes=2-4', 'cde', 'bytes 2-4/9'],
  ['bytes=-3', 'ghi', 'bytes 6-8/9'], ['bytes=6-', 'ghi', 'bytes 6-8/9'],
]) {
  test(`remote file range ${range || 'full'} preserves content and headers`, async () => {
    const { handler, operations } = fixture()
    const response = await handler(new Request('lumi-remote://file/remote/home/%E4%B8%AD%20%23.txt', {
      headers: range ? { range } : {},
    }))
    assert.equal(response.status, range ? 206 : 200)
    assert.equal(response.headers.get('content-range'), contentRange)
    assert.equal(response.headers.get('content-length'), String(expected.length))
    assert.equal(await response.text(), expected)
    assert.ok(operations.every(operation => operation.path === '/home/中 #.txt'))
  })
}

test('HEAD only reads file metadata; invalid ranges do not read file content', async () => {
  const { handler, operations } = fixture()
  const head = await handler(new Request('lumi-remote://file/remote/a.txt', { method: 'HEAD' }))
  assert.equal(await head.text(), '')
  assert.equal(head.headers.get('content-length'), '9')
  const invalid = await handler(new Request('lumi-remote://file/remote/a.txt', { headers: { range: 'bytes=99-' } }))
  assert.equal(invalid.status, 416)
  assert.equal(invalid.headers.get('content-range'), 'bytes */9')
  assert.equal(operations.filter(operation => operation.op === 'file_read').length, 0)
})

test('remote file errors preserve status and CORS headers', async () => {
  const handler = createRemoteFileHandler({ request: async () => { throw Object.assign(Error('denied'), { status: 403 }) } }, () => ({}))
  const response = await handler(new Request('lumi-remote://file/remote/a.txt'))
  assert.equal(response.status, 403)
  assert.equal(response.headers.get('access-control-allow-origin'), '*')
})

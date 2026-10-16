import test from 'node:test'
import assert from 'node:assert/strict'
import { directoryCrumbs } from '../src/lib/directory.ts'

test('POSIX breadcrumbs preserve spaces, unicode and repeated separators', () => {
  assert.deepEqual(directoryCrumbs('/home/用户/My project'), [
    { label: '/', path: '/' }, { label: 'home', path: '/home' },
    { label: '用户', path: '/home/用户' }, { label: 'My project', path: '/home/用户/My project' },
  ])
  assert.equal(directoryCrumbs('/home//work')[2].path, '/home//work')
})
test('drive and UNC breadcrumbs keep navigable roots and server separators', () => {
  assert.deepEqual(directoryCrumbs('C:\\Users\\demo').map(item => item.path), ['C:\\', 'C:\\Users', 'C:\\Users\\demo'])
  assert.deepEqual(directoryCrumbs('C:/Users/demo').map(item => item.path), ['C:/', 'C:/Users', 'C:/Users/demo'])
  assert.deepEqual(directoryCrumbs('\\\\server\\share\\folder').map(item => item.path), ['\\\\server\\share\\', '\\\\server\\share\\folder'])
  assert.deepEqual(directoryCrumbs(''), [])
})

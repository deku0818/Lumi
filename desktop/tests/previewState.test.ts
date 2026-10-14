import assert from 'node:assert/strict'
import { test } from 'node:test'
import { emptyPreviewState, previewReducer, type PreviewState } from '../src/lib/previewState.ts'
import type { Artifact } from '../src/types.ts'

const owner = 'local\0thread-A'
const caster = { path: '/project/caster.html', name: 'caster.html' }
const drive = { path: '/project/drive.html', name: 'drive.html' }
const frame = { path: '/project/frame.html', name: 'frame.html' }
function open(state: PreviewState, file: Artifact, session = owner): PreviewState {
  return previewReducer(state, { type: 'open', owner: session, file })
}

test('reopening a file focuses its existing tab and refreshes metadata without shrinking', () => {
  let state = open(open(emptyPreviewState, caster), drive)
  state = previewReducer(state, { type: 'toggle-expanded' })
  state = open(state, { ...caster, name: 'Updated caster' })
  assert.deepEqual(state.files.map(file => file.path), [caster.path, drive.path])
  assert.equal(state.files[0].name, 'Updated caster')
  assert.equal(state.activePath, caster.path)
  assert.equal(state.expanded, true)
})

test('closing an inactive tab preserves the selected file and expanded workspace', () => {
  const state = previewReducer(open(open(emptyPreviewState, caster), drive), { type: 'toggle-expanded' })
  const closed = previewReducer(state, { type: 'close', path: caster.path })
  assert.equal(closed.activePath, drive.path)
  assert.equal(closed.expanded, true)
  assert.deepEqual(state.files, [caster, drive])
})

test('closing the selected tab selects its next neighbor, then the preceding tab at the end', () => {
  let state = open(open(open(emptyPreviewState, caster), drive), frame)
  state = previewReducer(state, { type: 'select', path: drive.path })
  state = previewReducer(state, { type: 'close', path: drive.path })
  assert.equal(state.activePath, frame.path)
  state = previewReducer(state, { type: 'close', path: frame.path })
  assert.equal(state.activePath, caster.path)
})

test('closing the last tab exits expansion and releases the preview window', () => {
  const expanded = previewReducer(open(emptyPreviewState, caster), { type: 'toggle-expanded' })
  assert.deepEqual(previewReducer(expanded, { type: 'close', path: caster.path }), emptyPreviewState)
})

test('another machine or session cannot inherit tabs for the same absolute path', () => {
  const expanded = previewReducer(open(open(emptyPreviewState, caster), drive), { type: 'toggle-expanded' })
  for (const session of ['remote\0thread-A', 'local\0thread-B']) {
    const next = open(expanded, caster, session)
    assert.deepEqual(next.files, [caster])
    assert.equal(next.owner, session)
    assert.equal(next.expanded, false)
  }
})

test('shrinking keeps the file objects and selection; closing the window clears them', () => {
  const state = previewReducer(open(open(emptyPreviewState, caster), drive), { type: 'toggle-expanded' })
  const shrunk = previewReducer(state, { type: 'shrink' })
  assert.equal(shrunk.files, state.files)
  assert.equal(shrunk.activePath, drive.path)
  assert.equal(shrunk.expanded, false)
  assert.deepEqual(previewReducer(shrunk, { type: 'close-all' }), emptyPreviewState)
  assert.equal(previewReducer(emptyPreviewState, { type: 'toggle-expanded' }), emptyPreviewState)
})

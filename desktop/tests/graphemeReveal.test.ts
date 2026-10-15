import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Element, Root, RootContent } from 'hast'
import { revealNewText } from '../src/lib/graphemeReveal.ts'

function tree(value: string, tagName = 'p', offset = 0): Root {
  return { type: 'root', children: [{ type: 'element', tagName, properties: {}, children: [{
    type: 'text', value,
    position: { start: { line: 1, column: offset + 1, offset }, end: { line: 1, column: offset + value.length + 1, offset: offset + value.length } },
  }] }] }
}
function text(node: Root | RootContent): string {
  return node.type === 'text' ? node.value : 'children' in node ? node.children.map(text).join('') : ''
}
function spans(root: Root): Element[] {
  return (root.children[0] as Element).children.filter((n): n is Element => n.type === 'element')
}

test('only appended source text is animated, preserving the visible prefix', () => {
  const root = tree('已读资料，准备汇报')
  revealNewText(root, 5)
  assert.equal(text(root), '已读资料，准备汇报')
  assert.equal(spans(root).map(text).join(''), '准备汇报')
})
test('historical content and reparsed earlier nodes stay unanimated', () => {
  const root = tree('已经显示的正文', 'strong', 10)
  revealNewText(root, 30)
  assert.equal(spans(root).length, 0)
  assert.equal(text(root), '已经显示的正文')
})
test('new Unicode graphemes remain whole, including joined emoji and combining marks', () => {
  const root = tree('Hi 👩‍💻 e\u0301 中')
  revealNewText(root, 3)
  assert.deepEqual(spans(root).map(text), ['👩‍💻', ' ', 'e\u0301', ' ', '中'])
})
test('a streaming delta that extends an existing joined emoji never drops the cluster', () => {
  const root = tree('👩‍💻')
  revealNewText(root, 2)
  assert.equal(text(root), '👩‍💻')
  assert.equal(spans(root).length, 0)
})
test('code and preformatted highlighter trees remain unchanged', () => {
  for (const tag of ['code', 'pre']) {
    const root = tree('const x = 1', tag)
    const before = structuredClone(root)
    revealNewText(root, 0)
    assert.deepEqual(root, before)
  }
})
test('large batches do not accumulate seconds of reveal delay', () => {
  const root = tree('新'.repeat(100))
  revealNewText(root, 0)
  assert.equal(spans(root).length, 100)
  assert.equal(spans(root).at(-1)?.properties.style, 'animation-delay:180ms')
})

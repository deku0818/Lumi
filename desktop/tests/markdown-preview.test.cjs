const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')

const { renderMarkdown } = loadRenderer('tests/fixtures/markdown.tsx', {
  process, Buffer, TextEncoder, TextDecoder, setImmediate, clearImmediate,
  localStorage: { getItem: () => 'zh' }, navigator: { language: 'zh' },
}, ['react', 'react/*', 'react-dom', 'react-dom/*'])

test('resource previews preserve single newlines and separate paragraphs', () => {
  const html = renderMarkdown('第一行\n第二行\n\n新段落', true)
  assert.match(html, /<p>第一行<br\/>\n第二行<\/p>/)
  assert.match(html, /<p>新段落<\/p>/)
  assert.doesNotMatch(renderMarkdown('第一行\n第二行'), /<br/)
})

test('resource previews keep Markdown structure, code highlighting and literal code newlines', () => {
  const html = renderMarkdown('# 标题\n\n- **重点**\n  补充说明\n\n> 引用\n> 第二行\n\n```js\nconst a = 1\nconst b = 2\n```\n\n| 文件 |\n| --- |\n| SOUL.md |', true)
  assert.match(html, /<h1>标题<\/h1>/)
  assert.match(html, /<li><strong>重点<\/strong><br\/>\n补充说明<\/li>/)
  assert.match(html, /<blockquote>\n<p>引用<br\/>\n第二行<\/p>/)
  assert.match(html, /<table>/)
  assert.match(html, /<td>SOUL.md<\/td>/)
  const code = html.match(/<pre>([\s\S]*?)<\/pre>/)?.[1]
  assert.ok(code)
  assert.match(code, /hljs-keyword/)
  assert.match(code, /\n/)
  assert.doesNotMatch(code, /<br/)
})

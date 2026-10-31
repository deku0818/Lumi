const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')
const { parseNote, splitNoteLinks, formatNoteValue } = loadRenderer('src/lib/noteMarkdown.ts')
const { renderMarkdown, renderNote } = loadRenderer('tests/fixtures/markdown.tsx', {
  process, Buffer, TextEncoder, TextDecoder, setImmediate, clearImmediate,
  localStorage: { getItem: () => 'zh' }, navigator: { language: 'zh' },
}, ['react', 'react/*', 'react-dom', 'react-dom/*'])
const plain = value => JSON.parse(JSON.stringify(value))
const body = '# 写作草稿\n\n> 原文 [[梳理下vibe coding]]，`**待补**` 保持代码样式。\n'
const text = '---\nstatus: 骨架\nsource: "[[梳理下vibe coding]]"\ntags: [vibe-coding, 写作草稿]\n---\n\n' + body

test('note properties use YAML values and preserve the body verbatim', () => {
  const note = parseNote(text)
  assert.deepEqual(plain(note.properties), { status: '骨架', source: '[[梳理下vibe coding]]', tags: ['vibe-coding', '写作草稿'] })
  assert.equal(note.body, '\n' + body)
  const multiline = parseNote('---\nsummary: |\n  第一行\n  第二行\ncount: 0\nenabled: false\ndate: 2026-10-31\n---\n正文')
  assert.deepEqual(plain(multiline.properties), { summary: '第一行\n第二行\n', count: 0, enabled: false, date: '2026-10-31' })
})

test('BOM, CRLF and empty property blocks are supported', () => {
  assert.equal(parseNote('\uFEFF---\r\nstatus: 骨架\r\n---\r\n正文\r\n').body, '正文\r\n')
  for (const yaml of ['', '# comment\n']) {
    const note = parseNote('---\n' + yaml + '---\n正文')
    assert.deepEqual(plain(note.properties), {})
    assert.equal(note.body, '正文')
  }
})

test('ordinary Markdown and invalid properties retain all original content', () => {
  for (const source of [
    '# 正文\n\n---\nstatus: example\n---',
    '---\nstatus: [unclosed\n---\n正文',
    '---\nstatus: one\nstatus: two\n---\n正文',
    '---\n- root list\n---\n正文',
    '---\njust text\n---\n正文',
    '---\nstatus: 骨架\n正文',
    '~~~yaml\n---\nstatus: example\n---\n~~~',
    '---\nunsafe: !!js/function "function(){}"\n---\n正文',
  ]) {
    assert.deepEqual(plain(parseNote(source).properties), {})
    assert.equal(parseNote(source).body, source)
  }
})

test('property note links preserve targets and aliases without pretending to resolve files', () => {
  assert.deepEqual(plain(splitNoteLinks('来自 [[目录/笔记#段落|别名]]，以及 [[另一篇.md]]。')), [
    '来自 ', { target: '目录/笔记#段落', label: '别名' }, '，以及 ', { target: '另一篇.md', label: '另一篇.md' }, '。',
  ])
  for (const source of ['![[image.png]]', '\\[[literal]]', '[[ ]]']) assert.deepEqual(plain(splitNoteLinks(source)), [source])
})

test('nested and cyclic YAML values can be displayed without expanding aliases', () => {
  const note = parseNote('---\nvalue: &ref {self: *ref}\n---\n正文')
  assert.equal(note.properties.value.self, note.properties.value)
  assert.match(formatNoteValue(note.properties.value), /\*ref_0/)
  assert.equal(formatNoteValue(false), 'false')
  assert.equal(formatNoteValue(0), '0')
})

test('read-only properties and tags are separate from normal Markdown content', () => {
  const html = renderNote(text)
  assert.equal((html.match(/class="note-property"/g) || []).length, 3)
  assert.equal((html.match(/class="note-tag"/g) || []).length, 2)
  assert.ok(!html.includes('<input'))
  assert.ok(!html.includes('status: 骨架'))
  assert.match(html, /<h1>写作草稿<\/h1>/)
  assert.match(html, /href="#lumi-note\//)
  assert.ok(!renderNote('# 普通笔记').includes('note-properties'))
  assert.match(renderMarkdown(text), /<h2>status:/, 'Chat Markdown does not parse file properties')
})

test('wiki links opt in without changing ordinary file links or chat output', () => {
  const source = '[[目录/笔记#标题|别名]] [普通链接](ordinary.md)'
  const html = renderMarkdown(source, false, true)
  assert.match(html, /href="#lumi-note\/%E7%9B%AE%E5%BD%95%2F%E7%AC%94%E8%AE%B0%23%E6%A0%87%E9%A2%98"/)
  assert.match(html, />别名<\/a>/)
  assert.match(html, /<span>普通链接<\/span>/)
  assert.ok(renderMarkdown(source).includes('[[目录/笔记#标题|别名]]'))
  assert.match(renderMarkdown('[[javascript:alert(1)]]', false, true), /href="#lumi-note\/javascript%3A/)
})

test('escaped links, inline code, fenced code and embeds remain literal', () => {
  const source = '\\[\\[literal\\]\\] and [[real]]\n\n`[[inline]]`\n\n~~~yaml\nsource: "[[code]]"\n~~~\n\n![[image.png]]'
  const html = renderMarkdown(source, false, true)
  assert.ok(html.includes('[[literal]] and <a'))
  assert.ok(html.includes('<code>[[inline]]</code>'))
  assert.ok(html.includes('[[code]]'))
  assert.ok(html.includes('![[image.png]]'))
  assert.equal((html.match(/href="#lumi-note\//g) || []).length, 1)
})

test('property HTML is text and cannot become active markup', () => {
  const html = renderNote('---\nstatus: "<img src=x onerror=alert(1)>"\n---\n正文')
  assert.ok(!html.includes('<img'))
  assert.ok(html.includes('&lt;img'))
})

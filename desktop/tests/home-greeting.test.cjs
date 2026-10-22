const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')
const { homeGreetingFrame } = loadRenderer('src/lib/homeGreeting.ts')

const phrases = ['今天想让 Lumi 帮你做些什么？', '有个新点子？和 Lumi 一起试试', '想写点什么？从一句话开始']

test('the cursor disappears as soon as the complete greeting is visible', () => {
  const start = homeGreetingFrame(phrases, 0, 90)
  assert.equal(start.text, '')
  assert.equal(start.cursor, true)
  assert.equal(homeGreetingFrame(phrases, 90, 90).text, '今')

  for (const time of [3000, 30_000, 58_000]) {
    const frame = homeGreetingFrame(phrases, time, 90)
    assert.equal(frame.text, phrases[0])
    assert.equal(frame.cursor, false)
  }
  assert(homeGreetingFrame(phrases, 3000, 90).nextIn > 50_000, 'The idle greeting does not need character or frame polling')
})

test('deletion preserves the prefix and hides the cursor during the empty gap', () => {
  const deleting = homeGreetingFrame(phrases, 59_300, 90)
  assert(deleting.text.length > 0 && deleting.text.length < phrases[0].length)
  assert(phrases[0].startsWith(deleting.text))
  assert.equal(deleting.cursor, true)
  const gap = homeGreetingFrame(phrases, 59_900, 90)
  assert.equal(gap.text, '')
  assert.equal(gap.cursor, false)
})

test('each new phrase starts at a one-minute boundary and the list loops', () => {
  for (let index = 1; index <= phrases.length; index++) {
    const time = index * 60_000
    assert.equal(homeGreetingFrame(phrases, time - 1, 90).fullText, phrases[(index - 1) % phrases.length])
    const next = homeGreetingFrame(phrases, time, 90)
    assert.equal(next.fullText, phrases[index % phrases.length])
    assert.equal(next.text, '')
    assert.equal(next.cursor, true)
  }
})

test('English typing uses the same minute cadence, including after a delayed timer', () => {
  const english = ['What would you like Lumi to help with today?', 'A new idea? Let’s explore it with Lumi.']
  assert.equal(homeGreetingFrame(english, 50, 50).text, 'W')
  const complete = homeGreetingFrame(english, 3000, 50)
  assert.equal(complete.text, english[0])
  assert.equal(complete.cursor, false)
  const delayed = homeGreetingFrame(english, 180_050, 50)
  assert.equal(delayed.fullText, english[1])
  assert.equal(delayed.text, 'A')
})

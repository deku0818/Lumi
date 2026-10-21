const test = require('node:test')
const assert = require('node:assert/strict')
const { loadRenderer } = require('./renderer-module.cjs')

function randomMath() {
  let seed = 27
  const math = Object.create(Math)
  math.random = () => {
    seed = seed * 16807 % 2147483647
    return (seed - 1) / 2147483646
  }
  return math
}

test('snow randomly melts during entry, mid-flight or after landing, without teleporting', () => {
  const { createSnowflake, snowflakeFrame } = loadRenderer('src/lib/homeBackdrop/snowParticles.ts', { Math: randomMath() })
  const modes = new Set()
  for (let i = 0; i < 200; i++) {
    const flake = createSnowflake(100, 600, false)
    const landedAt = flake.duration * flake.landAt
    modes.add(flake.meltMode)
    assert.equal(snowflakeFrame(flake, 100 + flake.meltStart - .001, 800, 600).melt, 0)
    const melting = snowflakeFrame(flake, 100 + flake.meltStart + flake.meltDuration / 2, 800, 600)
    assert(Math.abs(melting.melt - .5) < 1e-10)
    assert(snowflakeFrame(flake, 100 + flake.meltStart + flake.meltDuration + .001, 800, 600).finished)
    if (flake.meltMode === 'ground') {
      assert(flake.meltStart > landedAt)
      const landed = snowflakeFrame(flake, 100 + landedAt + .1, 800, 600)
      assert(landed.settled && landed.melt === 0)
      assert.equal(melting.x, landed.x)
      assert.equal(melting.y, landed.y)
    } else {
      assert(flake.meltStart < landedAt)
    }
    const before = snowflakeFrame(flake, 100 + landedAt - .0001, 800, 600)
    const after = snowflakeFrame(flake, 100 + landedAt + .0001, 800, 600)
    assert(Math.abs(before.x - after.x) < .01 && Math.abs(before.y - after.y) < .01)
    assert(flake.scale <= .5, 'Keep the approved small snowflake sizes')
  }
  assert.deepEqual([...modes].sort(), ['early', 'ground', 'midair'])
})

test('crystals erode from the outside without scaling, moving or fading the surviving glyphs', () => {
  const { drawCrystal } = loadRenderer('src/lib/homeBackdrop/snowCrystal.ts')
  function draw(melt) {
    const points = new Map(), scales = []
    const ctx = {
      save() {}, restore() {}, translate() {}, rotate() {},
      scale(x, y) { scales.push([x, y]) },
      fillText(glyph, x, y) { points.set(`${x},${y}`, { radius: Math.hypot(x, y), alpha: this.globalAlpha }) },
    }
    drawCrystal(ctx, 0, 0, 0, .4, 0, melt, .5, '#6b6863')
    return { points, scales }
  }
  const full = draw(0)
  let previous = full
  for (const melt of [.35, .7, .95]) {
    const current = draw(melt)
    assert.deepEqual(current.scales, full.scales)
    assert(current.points.size < previous.points.size)
    assert(Math.max(...[...current.points.values()].map(point => point.radius)) < Math.max(...[...previous.points.values()].map(point => point.radius)))
    for (const [position, point] of current.points) assert.equal(point.alpha, full.points.get(position).alpha)
    previous = current
  }
  assert.equal(draw(1).points.size, 0)
})

test('meteors continue straight ahead while their head and trail fade', () => {
  const math = Object.create(Math)
  math.random = () => .5
  const { NightSky } = loadRenderer('src/lib/homeBackdrop/night.ts', { Math: math })
  const night = new NightSky()
  night.palette('#ffcc00', '#ececea', '#42a5f5')
  let heads = []
  const ctx = {
    shadowBlur: 0,
    fillText(glyph, x, y) {
      if (glyph === '*' && this.shadowBlur > 0) heads.push({ x, y, alpha: this.globalAlpha })
    },
  }
  const frame = time => {
    heads = []
    night.paint(ctx, 800, 600, time, .85, false)
    return heads[0]
  }
  const bright = frame(1.2), fading = frame(4), later = frame(4.2)
  assert(fading.x < bright.x && fading.y > bright.y)
  assert(later.x < fading.x && later.y > fading.y, 'Do not stop when fading starts')
  assert(Math.abs((later.y - fading.y) / (fading.x - later.x) - .62) < 1e-10)
  assert(fading.alpha < bright.alpha && later.alpha < fading.alpha)
})

function backdropFixture(lightInitially = false) {
  const documentEvents = new Map(), windowEvents = new Map(), mediaEvents = new Map(), frames = new Map()
  const observers = [], offscreen = []
  let nextFrame = 0, clock = 0, light = lightInitially, width = 800, bottom = 430
  function context() {
    return new Proxy({
      globalAlpha: 1, messages: [], clips: [], path: [], pose: [0, 0, 0], stack: [], clears: [], texts: 0,
      save() { this.stack.push({ pose: this.pose.slice(), alpha: this.globalAlpha }) },
      restore() { const value = this.stack.pop(); this.pose = value.pose; this.globalAlpha = value.alpha },
      translate(x, y) { this.pose[0] += x; this.pose[1] += y },
      rotate(angle) { this.pose[2] += angle },
      fillText(glyph, x, y) { this.texts++; this.messages.push([glyph, x, y, this.globalAlpha]) },
      drawImage(image, ...args) { this.messages.push(['image', ...this.pose, ...args, image.getContext('2d').messages.length]) },
      clearRect() { this.messages = []; this.clears.push(clock) },
      beginPath() { this.path = [] }, rect(...args) { this.path.push(args) },
      clip(rule) { this.clips.push({ rule, rects: this.path.slice() }) },
      createRadialGradient: () => ({ addColorStop() {} }),
    }, { get: (object, key) => object[key] ?? (() => {}) })
  }
  function canvas() {
    const ctx = context(), values = { width: 300, height: 150 }
    const target = { writes: 0, getContext: () => ctx, getBoundingClientRect: () => ({ left: 100, top: 0, width, height: 600 }) }
    for (const name of ['width', 'height']) Object.defineProperty(target, name, { get: () => values[name], set(value) { target.writes++; values[name] = value } })
    return target
  }
  const background = canvas(), detail = canvas()
  const composer = { getBoundingClientRect: () => ({ left: 150, right: 850, top: 300, bottom }) }
  background.closest = () => ({ querySelector: () => composer })
  const reduced = { matches: false, addEventListener: (name, callback) => mediaEvents.set(name, callback), removeEventListener: name => mediaEvents.delete(name) }
  const document = {
    hidden: false, documentElement: { classList: { contains: () => light } },
    createElement: () => { const target = canvas(); offscreen.push(target); return target },
    addEventListener: (name, callback) => documentEvents.set(name, callback), removeEventListener: name => documentEvents.delete(name),
  }
  class Observer {
    constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this) }
    observe() {} disconnect() { this.disconnected = true }
  }
  const { mountHomeBackdrop } = loadRenderer('src/lib/homeBackdrop.ts', {
    document, Math: randomMath(), devicePixelRatio: 2,
    window: { addEventListener: (name, callback) => windowEvents.set(name, callback), removeEventListener: name => windowEvents.delete(name) },
    getComputedStyle: () => ({ getPropertyValue: name => ({ '--color-accent': '#ffcc00', '--color-ink': '#ececea', '--color-muted': '#a4a19b', '--color-info': '#42a5f5' })[name] }),
    matchMedia: () => reduced, performance: { now: () => clock }, ResizeObserver: Observer, MutationObserver: Observer,
    requestAnimationFrame: callback => { const id = ++nextFrame; frames.set(id, callback); return id }, cancelAnimationFrame: id => frames.delete(id),
  })
  const dispose = mountHomeBackdrop(background, detail)
  return {
    background, detail, offscreen, observers, frames, reduced, document, documentEvents, windowEvents, mediaEvents, dispose,
    at(time) { clock = time; const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback(time)) },
    theme(value) { light = value; observers[1].callback() },
    width(value) { width = value; windowEvents.get('resize')() },
    composerBottom(value) { bottom = value; observers[0].callback() },
  }
}

for (const refresh of [60, 120]) {
  test(`30 FPS pacing retains fractional time on a ${refresh} Hz display`, () => {
    const env = backdropFixture(); env.at(0)
    const ctx = env.background.getContext('2d'); ctx.clears.length = 0
    for (let i = 1; i <= refresh * 5; i++) env.at(i * 1000 / refresh)
    assert.equal(ctx.clears.length, 150)
    for (let i = 1; i < ctx.clears.length; i++) assert(Math.abs(ctx.clears[i] - ctx.clears[i - 1] - 1000 / 30) < .001)
    env.dispose()
  })
}

test('composer-only geometry updates coalesce and preserve canvas buffers and winter scenery', () => {
  const env = backdropFixture(true); env.at(0)
  const writes = env.background.writes + env.detail.writes
  const winter = env.offscreen[0], winterWrites = winter.writes, winterTexts = winter.getContext('2d').texts
  for (let i = 0; i < 20; i++) env.composerBottom(450)
  env.windowEvents.get('resize')()
  assert.equal(env.frames.size, 1)
  env.at(0)
  assert.equal(env.background.writes + env.detail.writes, writes)
  assert.equal(winter.writes, winterWrites)
  assert.equal(winter.getContext('2d').texts, winterTexts)
  const clip = env.detail.getContext('2d').clips.at(-1)
  assert.equal(clip.rule, 'evenodd')
  assert.deepEqual(clip.rects, [[0, 0, 800, 600], [58, 308, 684, 134]])
  env.dispose()
})

test('the backdrop pauses when hidden or reduced, preserves inactive themes, and releases observers', () => {
  const env = backdropFixture(); env.at(0)
  assert.equal(env.background.width, 1600); assert.equal(env.frames.size, 1)
  env.document.hidden = true; env.documentEvents.get('visibilitychange')(); assert.equal(env.frames.size, 0)
  env.document.hidden = false; env.documentEvents.get('visibilitychange')(); env.at(0)
  env.reduced.matches = true; env.mediaEvents.get('change')(); env.at(0); assert.equal(env.frames.size, 0)
  env.theme(true); env.at(0); assert.equal(env.frames.size, 0)
  env.reduced.matches = false; env.mediaEvents.get('change')(); env.at(0); env.at(40)
  const snow = env.detail.getContext('2d').messages.slice(); assert(snow.length > 0)
  env.theme(false); env.at(40)
  for (let i = 1; i <= 30; i++) env.at(40 + i * 100)
  env.theme(true); env.at(3040); assert.deepEqual(env.detail.getContext('2d').messages, snow)
  env.width(0); env.at(3040); assert.equal(env.frames.size, 0)
  env.width(600); env.at(3040); assert.equal(env.background.width, 1200); assert.equal(env.frames.size, 1)
  env.dispose(); assert.equal(env.frames.size, 0)
  assert(env.observers.every(observer => observer.disconnected))
  assert.equal(env.documentEvents.size + env.windowEvents.size + env.mediaEvents.size, 0)
})

test('crystal cache reuses motion frames while preserving every actual melt and tone change', () => {
  const rasters = []
  const { CrystalCache, drawCrystal } = loadRenderer('src/lib/homeBackdrop/snowCrystal.ts', { document: { createElement() {
    const ctx = { points: [], writes: 0, setTransform() {}, clearRect() { this.points = [] }, fillText(glyph, x, y) { this.writes++; this.points.push([glyph, x, y, this.globalAlpha]) } }
    const canvas = { width: 0, height: 0, getContext: () => ctx }; rasters.push(canvas); return canvas
  } } })
  const cache = new CrystalCache(), particle = {}, poses = []
  const main = { save() {}, restore() {}, translate(x, y) { poses.push([x, y]) }, rotate() {}, drawImage() {} }
  cache.configure(2, '#6b6863')
  cache.draw(main, particle, 0, 10, 20, .4, .1, 0, .5)
  const raster = rasters[0].getContext('2d'), writes = raster.writes
  cache.draw(main, particle, 0, 11, 21, .4, .2, 0, .5)
  assert.equal(raster.writes, writes)
  assert.deepEqual(poses, [[10, 20], [11, 21]])
  for (const melt of [.12, .35, .7, .95]) {
    cache.draw(main, particle, 0, 11, 21, .4, .2, melt, .5)
    const reference = { points: [], save() {}, restore() {}, translate() {}, rotate() {}, scale() {}, fillText(glyph, x, y) { this.points.push([glyph, x, y, this.globalAlpha]) } }
    drawCrystal(reference, 0, 0, 0, 1, 0, melt, .5, '#6b6863')
    assert.deepEqual(raster.points, reference.points)
  }
  cache.configure(2, '#6b6863'); cache.draw(main, particle, 0, 11, 21, .4, .2, .95, .3)
  assert.equal(rasters.length, 1)
  cache.configure(1, '#6b6863'); cache.draw(main, particle, 0, 11, 21, .4, .2, .95, .3)
  assert.equal(rasters.length, 2)
  assert.equal(rasters[1].width, rasters[0].width / 2)
})

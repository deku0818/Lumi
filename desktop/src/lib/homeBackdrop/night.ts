type Meteor = { x: number; y: number; length: number; travel: number; start: number; gold: boolean; seed: number }
type Star = { x: number; y: number; close: boolean; gold: boolean; brightness: number; start: number; life: number; phase: number; speed: number }
const BURN_DURATION = 2.15
const FADE_DURATION = 3.4
const METEOR_DURATION = BURN_DURATION + FADE_DURATION
const noise = (x: number, y: number): number => {
  const value = Math.sin(x * 127.1 + y * 311.7) * 43758.5453
  return value - Math.floor(value)
}

export class NightSky {
  private accent = ''
  private ink = ''
  private haze = ''
  private meteors: Meteor[] = []
  private stars: Star[] = []
  private nextMeteor = 0
  private lastTime = -1
  private fieldSeed = Math.random() * 10000
  palette(accent: string, ink: string, info: string): void {
    this.accent = accent; this.ink = ink
    this.haze = `color-mix(in srgb, ${ink} 80%, ${info})`
  }

  private newMeteor(start: number): Meteor {
    return {
      x: .52 + Math.random() * .62, y: Math.random() * .72,
      length: 105 + Math.random() * 80, travel: .24 + Math.random() * .13,
      start, gold: Math.random() < .3, seed: Math.random() * 10000,
    }
  }

  private newStar(time: number, initial: boolean): Star {
    const life = 18 + Math.random() * 30
    return {
      x: Math.random(), y: Math.random(), close: Math.random() < .055, gold: Math.random() < .08,
      brightness: .14 + Math.random() * .34, life, start: time - (initial ? Math.random() * life : 0),
      phase: Math.random() * Math.PI * 2, speed: .22 + Math.random() * .4,
    }
  }

  paint(ctx: CanvasRenderingContext2D, width: number, height: number, time: number, intensity: number, reduced: boolean): void {
    const clock = reduced ? 1.2 : time
    if (this.lastTime < 0 || clock < this.lastTime) {
      this.meteors = [this.newMeteor(clock - .7 - Math.random()), this.newMeteor(clock - 2.6 - Math.random())]
      this.nextMeteor = clock + 1.2 + Math.random() * 2.4
      this.stars = []
    }
    this.lastTime = clock
    this.meteors = this.meteors.filter(meteor => clock - meteor.start < METEOR_DURATION)
    if (!reduced && clock >= this.nextMeteor) {
      this.meteors.push(this.newMeteor(clock))
      this.nextMeteor = clock + 1.4 + Math.random() * 2.8
    }
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    // Two sparse filaments keep the centre open while adding depth around the input.
    ctx.font = '7px Menlo,monospace'
    for (let y = 7; y < height; y += 9) for (let x = 5; x < width; x += 6) {
      const nx = x / width, ny = y / height
      const cloud = .15 + nx * .16 + Math.sin(nx * 6 + clock * .012) * .04
      const density = Math.exp(-((ny - cloud) ** 2) / .0018)
      const lower = Math.exp(-((ny - .94 + nx * .11) ** 2) / .001)
      const value = noise(x + this.fieldSeed, y)
      if (value > Math.max(density, lower) * .52) continue
      ctx.globalAlpha = (.025 + value * .1) * intensity
      ctx.fillStyle = value > .3 ? this.haze : this.ink
      ctx.fillText(value > .4 ? ':' : '.', x, y)
    }
    const count = Math.min(320, Math.round(width * height / 2300))
    while (this.stars.length < count) this.stars.push(this.newStar(clock, true))
    for (let i = 0; i < count; i++) {
      if (clock >= this.stars[i].start + this.stars[i].life) this.stars[i] = this.newStar(clock, false)
      const star = this.stars[i]
      const x = star.x * width, y = star.y * height
      const age = (clock - star.start) / star.life
      const envelope = Math.sin(Math.min(1, Math.max(0, age)) * Math.PI) ** .7
      const flicker = .65 + .35 * Math.sin(clock * star.speed + star.phase) ** 2
      ctx.fillStyle = star.gold ? this.accent : this.ink
      ctx.globalAlpha = star.brightness * flicker * envelope * intensity
      ctx.font = `${star.close ? 9 : 7}px Menlo,monospace`
      ctx.fillText(star.close ? '+' : '.', x, y)
      if (star.close) {
        ctx.globalAlpha *= .23
        ctx.fillText('.', x - 5, y); ctx.fillText('.', x + 5, y)
        ctx.fillText("'", x, y - 5); ctx.fillText('.', x, y + 5)
      }
    }
    for (const meteor of this.meteors) {
      this.meteor(ctx, width, height, meteor, clock - meteor.start, intensity)
    }
    ctx.globalAlpha = 1
  }

  private meteor(ctx: CanvasRenderingContext2D, width: number, height: number, meteor: Meteor, phase: number, intensity: number): void {
    const travel = phase / BURN_DURATION
    const fade = Math.max(0, Math.min(1, (phase - BURN_DURATION) / FADE_DURATION))
    const headX = meteor.x * width - travel * width * meteor.travel
    const headY = meteor.y * height + travel * width * meteor.travel * .62
    const length = Math.min(meteor.length, width * .28)
    const envelope = Math.min(1, phase / .3) * (1 - fade * fade * (3 - 2 * fade))
    const cellW = 4.5, cellH = 6
    const norm = Math.hypot(1, .62), ux = 1 / norm, uy = -.62 / norm
    ctx.font = '7px Menlo,monospace'
    const color = meteor.gold ? this.accent : this.ink
    // Preserve the straight glyph silhouette while its core and tail fade together.
    for (let y = Math.floor((headY - length * .62 - 30) / cellH) * cellH; y < headY + 30; y += cellH) {
      for (let x = Math.floor((headX - 18) / cellW) * cellW; x < headX + length + 20; x += cellW) {
        if (x < 0 || y < 0 || x > width || y > height) continue
        const dx = x - headX, dy = y - headY
        const along = dx * ux + dy * uy
        if (along < -6 || along > length) continue
        const t = Math.max(0, along / length)
        const across = dx * -uy + dy * ux
        const coreWidth = 2.2 + (1 - t) * 2.3
        const plumeWidth = coreWidth + 4
        if (Math.abs(across) > plumeWidth) continue
        const value = noise(Math.floor(along / 4) + meteor.seed, Math.round(across / 3))
        if (Math.abs(across) > coreWidth && value < .7) continue
        const core = Math.exp(-(across ** 2) / (coreWidth ** 2))
        const edge = Math.max(0, 1 - Math.abs(across) / plumeWidth)
        let alpha = ((1 - t) ** .9) * envelope * (core * .8 + edge * .12)
        if (x > width * .12 && x < width * .88 && y > height * .28 && y < height * .47) alpha *= .4
        ctx.globalAlpha = alpha * intensity
        ctx.fillStyle = core > .45 ? color : this.haze
        const ramp = '#*+=:.'
        const glyph = ramp[Math.min(ramp.length - 1, Math.floor(t * (ramp.length - 1) + (1 - core) * 1.5))]
        ctx.fillText(glyph, x, y)
      }
    }
    // A short, fine diffraction cross on the head avoids an oversized solid symbol.
    if (envelope > 0) {
      ctx.fillStyle = color; ctx.globalAlpha = envelope * intensity
      ctx.shadowColor = color; ctx.shadowBlur = 8 * (1 - fade)
      ctx.fillText('*', headX, headY)
      ctx.shadowBlur = 0; ctx.globalAlpha *= .25
      ctx.fillText('.', headX - 5, headY); ctx.fillText('.', headX + 5, headY)
    }
    ctx.globalAlpha = 1
  }

}

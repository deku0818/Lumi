import { createSnowflake, snowflakeFrame, type Snowflake } from './snowParticles'
import { CrystalCache } from './snowCrystal'

export type ComposerBounds = Readonly<{ left: number; right: number; top: number; bottom: number }>
type Surface = { anchor: number; position: number; level: number; scale: number; opacity: number; start: number; duration: number; shape: number; angle: number; seed: number }

function paintWinter(ctx: CanvasRenderingContext2D, width: number, height: number, accent: string, snowColor: string): void {
  // A low winter landscape frames the input instead of scattering decoration uniformly.
  const sunX = width * .82, sunY = height * .13
  const glow = ctx.createRadialGradient(sunX, sunY, 2, sunX, sunY, 125)
  glow.addColorStop(0, `color-mix(in srgb, ${accent} 12%, transparent)`)
  glow.addColorStop(.35, `color-mix(in srgb, ${accent} 5%, transparent)`)
  glow.addColorStop(1, 'transparent')
  ctx.fillStyle = glow
  ctx.fillRect(sunX - 125, sunY - 125, 250, 250)
  ctx.font = '9px Menlo,monospace'
  ctx.fillStyle = snowColor
  for (let x = 4; x < width; x += 7) {
    const ridge = height * (.79 + .035 * Math.sin(x / width * 6 + .5) + .018 * Math.sin(x / width * 16))
    // Fine character shading suggests distant snow slopes through the mist.
    for (let y = Math.floor(ridge / 10) * 10; y < height; y += 10) {
      const noise = Math.abs(Math.sin(x * 31.7 + y * 78.3))
      const band = (y - ridge) / height
      if (noise < .64 || band > .075) continue
      ctx.globalAlpha = .11 - band
      ctx.fillText(noise > .9 ? ':' : '.', x, y)
    }
    ctx.globalAlpha = .14
    ctx.fillText(x % 3 ? '.' : '-', x, ridge)
  }
  const trees = [
    [.025, .96, .25, .36], [.11, .92, .19, .29], [.18, .89, .12, .2],
    [.28, .84, .065, .11], [.35, .82, .05, .09], [.57, .82, .04, .08],
    [.73, .86, .07, .14], [.86, .92, .18, .26], [.97, .99, .3, .38],
  ]
  for (const [position, base, size, opacity] of trees) {
    const treeX = width * position, treeHeight = height * size, bottom = height * base
    const top = bottom - treeHeight
    for (let y = Math.floor(top / 9) * 9; y < bottom; y += 9) {
      const progress = (y - top) / treeHeight
      if (progress < 0) continue
      // Staggered boughs create a pine silhouette with snow on its upper edges.
      const tier = Math.min(1, Math.floor(progress * 5 + 1) / 5)
      const local = (progress * 5) % 1
      const half = treeHeight * .25 * tier * (.47 + local * .53)
      for (let x = Math.floor((treeX - half) / 6) * 6; x <= treeX + half; x += 6) {
        const edge = Math.abs(x - treeX) / Math.max(half, 1)
        const noise = Math.abs(Math.sin(x * 8.2 + y * 3.1))
        if (noise < .16) continue
        ctx.globalAlpha = opacity * (.55 + noise * .45)
        let glyph = noise > .75 ? '*' : ':'
        if (edge > .7) glyph = x < treeX ? '/' : '\\'
        ctx.fillText(glyph, x, y)
      }
    }
    ctx.globalAlpha = opacity * .65
    for (let y = bottom - treeHeight * .15; y < bottom + 7; y += 9) ctx.fillText('|', treeX, y)
  }
  ctx.globalAlpha = 1
}


class SnowSurfaces {
  private width = 0
  private height = 0
  private surfaces: Surface[] = []
  private color = ''
  private lastTime = -1
  private composer = { left: 0, right: 0, top: 0 }

  resize(width: number, height: number, color: string, composer: ComposerBounds): void {
    this.width = width; this.height = height; this.color = color
    this.composer = composer
  }

  private newSurface(time: number, anchor: number, initial: boolean): Surface {
    const scale = anchor < 2 ? .3 + Math.random() * .18 : .4 + Math.random() * .34
    const duration = 23 + Math.random() * 14
    return {
      anchor, position: Math.random(), level: .88 + Math.random() * .065,
      scale, opacity: .2 + scale * .45 + Math.random() * .08,
      start: initial ? time - Math.random() * duration : time + 1 + Math.random() * 7,
      duration, shape: Math.floor(Math.random() * 3), angle: Math.random() * Math.PI,
      seed: Math.random() * 10000,
    }
  }

  paint(ctx: CanvasRenderingContext2D, time: number, intensity: number, reduced: boolean, crystals: CrystalCache): void {
    const cycle = reduced ? 9 : time
    if (this.lastTime < 0 || cycle < this.lastTime) {
      this.surfaces = Array.from({ length: 4 }, (_, anchor) => this.newSurface(cycle, anchor, true))
    }
    this.lastTime = cycle
    ctx.fillStyle = this.color
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.font = '7px Menlo,monospace'
    for (let index = 0; index < this.surfaces.length; index++) {
      let surface = this.surfaces[index]
      let phase = (cycle - surface.start) / surface.duration * 28
      if (!reduced && phase >= 28) {
        surface = this.newSurface(cycle, surface.anchor, false)
        this.surfaces[index] = surface
        phase = (cycle - surface.start) / surface.duration * 28
      }
      if (phase < 0) continue
      let targetX = this.width * (.1 + surface.position * .8)
      if (surface.anchor === 0) targetX = this.composer.left + surface.position * 12
      else if (surface.anchor === 1) targetX = this.composer.right - surface.position * 12
      const targetY = surface.anchor < 2 ? this.composer.top - 3 : this.height * surface.level
      const fall = Math.min(1, phase / 7)
      const melt = Math.max(0, Math.min(1, (phase - 12) / 11))
      const arrive = Math.sin(fall * Math.PI / 2)
      const x = targetX + (1 - fall) * (Math.sin(time * .3 + surface.angle) * 25 - 32)
      const y = targetY - surface.scale * 18 - (1 - arrive) * this.height * .3
      if (phase < 24) crystals.draw(ctx, surface, surface.shape, x, y, surface.scale, (1 - fall) * .35 + surface.angle, melt, intensity * surface.opacity)
      if (phase > 7) {
        // Small snow clusters flatten into a wet, sparse edge as their flakes melt.
        for (let cell = -5; cell <= 5; cell++) {
          const weight = Math.max(0, 1 - Math.abs(cell) / 6)
          const noise = Math.abs(Math.sin(cell * 77 + surface.seed))
          const erosion = Math.max(0, (phase - 15) / 10)
          if (Math.abs(cell) / 5 + noise * .1 > (1 - erosion) * 1.1) continue
          ctx.globalAlpha = intensity * surface.opacity * .6
          ctx.font = '7px Menlo,monospace'
          let glyph = cell % 2 ? '.' : ','
          if (phase < 16) glyph = ':*+.'[(cell + 5) % 4]
          ctx.fillText(glyph, targetX + cell * 4, targetY - weight * (1 - erosion) * 3)
        }
      }
      if (phase > 19) {
        const droplet = (phase - 19) / 9
        ctx.globalAlpha = intensity * surface.opacity * .6
        ctx.font = `${7 * (1 - droplet)}px Menlo,monospace`
        ctx.fillText(droplet < .5 ? 'o' : '.', targetX + 3, targetY + droplet ** 2 * 25)
      }
      ctx.globalAlpha = 1
    }
  }

}

export class SnowScene {
  private crystals = new CrystalCache()
  private flakes: Snowflake[] = []
  private lastTime = -1
  private surfaces = new SnowSurfaces()
  private background = document.createElement('canvas')
  private snowColor = ''
  private backgroundKey = ''

  resize(width: number, height: number, dpr: number, accent: string, muted: string, info: string, composer: ComposerBounds): void {
    this.snowColor = `color-mix(in srgb, ${muted} 78%, ${info})`
    this.crystals.configure(dpr, this.snowColor)
    const key = `${width}/${height}/${dpr}/${accent}/${this.snowColor}`
    if (key !== this.backgroundKey) {
      this.backgroundKey = key
      this.background.width = Math.round(width * dpr)
      this.background.height = Math.round(height * dpr)
      const ctx = this.background.getContext('2d')!
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      paintWinter(ctx, width, height, accent, this.snowColor)
    }
    this.surfaces.resize(width, height, this.snowColor, composer)
  }

  paint(ctx: CanvasRenderingContext2D, foreground: CanvasRenderingContext2D, width: number, height: number, time: number, intensity: number, reduced: boolean): void {
    ctx.save()
    ctx.globalAlpha = intensity
    ctx.drawImage(this.background, 0, 0, width, height)
    ctx.restore()
    const clock = reduced ? 1.2 : time
    if (clock < this.lastTime) this.flakes = []
    this.lastTime = clock
    const count = Math.min(380, Math.round(width * height / 4300))
    while (this.flakes.length < count) this.flakes.push(createSnowflake(clock, height, true))
    this.flakes.length = count
    ctx.fillStyle = this.snowColor
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    for (let index = 0; index < count; index++) {
      let flake = this.flakes[index]
      let state = snowflakeFrame(flake, clock, width, height)
      if (!reduced && state.finished) {
        flake = createSnowflake(clock, height, false)
        this.flakes[index] = flake
        state = snowflakeFrame(flake, clock, width, height)
      }
      const { x, y, melt, motionAge } = state
      if (!reduced && (x < -50 || x > width + 50)) {
        this.flakes[index] = createSnowflake(clock, height, false)
        continue
      }
      if (melt >= 1) continue
      const opacity = [.2, .36, .58][flake.layer] * (.8 + .2 * Math.sin(flake.phase) ** 2) * intensity
      if (flake.layer > 0) {
        const shape = Math.floor(flake.phase * 10)
        this.crystals.draw(ctx, flake, shape % 3, x, y, flake.scale, Math.sin(motionAge * .16 + shape) * .3 + shape, melt, opacity)
      } else {
        ctx.save()
        ctx.globalAlpha = opacity
        ctx.font = '9px Menlo,monospace'
        if (melt > 0) {
          ctx.beginPath()
          ctx.ellipse(x, y, 9 * .55 * (1 - melt), 9 * .65 * (1 - melt), 0, 0, Math.PI * 2)
          ctx.clip()
        }
        ctx.fillText('.', x, y)
        ctx.restore()
      }
    }
    ctx.globalAlpha = 1
    this.surfaces.paint(foreground, clock, intensity, reduced, this.crystals)
  }
}

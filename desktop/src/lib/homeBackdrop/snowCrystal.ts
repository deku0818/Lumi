type Cell = { x: number; y: number; radius: number; noise: number; glyph: string; shade: number }
type Segment = { ax: number; ay: number; bx: number; by: number }

function crystal(seed: number): Cell[] {
  const segments: Segment[] = []
  const radius = 43
  for (let arm = 0; arm < 6; arm++) {
    const angle = arm * Math.PI / 3 - Math.PI / 2
    const ux = Math.cos(angle), uy = Math.sin(angle)
    segments.push({ ax: 0, ay: 0, bx: ux * radius, by: uy * radius })
    for (const fraction of [.28, .49, .69]) {
      for (const side of [-1, 1]) {
        const branch = angle + side * Math.PI / 3
        const length = radius * (.34 - fraction * .21)
        const ax = ux * radius * fraction, ay = uy * radius * fraction
        const bx = ax + Math.cos(branch) * length, by = ay + Math.sin(branch) * length
        segments.push({ ax, ay, bx, by })
        if (fraction < .5) {
          const tx = ax + (bx - ax) * .55, ty = ay + (by - ay) * .55
          segments.push({ ax: tx, ay: ty, bx: tx + ux * 6, by: ty + uy * 6 })
        }
      }
    }
  }
  const cells: Cell[] = []
  for (let y = -44; y <= 44; y += 5.5) {
    for (let x = -44; x <= 44; x += 3.6) {
      let nearest = Infinity, selected = segments[0]
      for (const line of segments) {
        const dx = line.bx - line.ax, dy = line.by - line.ay
        const t = Math.max(0, Math.min(1, ((x - line.ax) * dx + (y - line.ay) * dy) / (dx * dx + dy * dy)))
        const distance = Math.hypot(x - line.ax - t * dx, y - line.ay - t * dy)
        if (distance < nearest) { nearest = distance; selected = line }
      }
      if (nearest > 2.55) continue
      const noise = (Math.sin(x * 18.31 + y * 37.27 + seed * 1.7) * 43758.5453) % 1
      const value = Math.abs(noise)
      const dx = selected.bx - selected.ax, dy = selected.by - selected.ay
      let glyph = '/'
      if (Math.abs(dx) < Math.abs(dy) * .35) glyph = '|'
      else if (Math.abs(dy) < Math.abs(dx) * .35) glyph = '-'
      else if (dx * dy > 0) glyph = '\\'
      if (nearest < 1.4) {
        glyph = '+'
        if (value > .83) glyph = '#'
        else if (value > .56) glyph = '*'
      }
      if (nearest > 2) glyph = ':'
      cells.push({ x, y, radius: Math.hypot(x, y) / radius, noise: value, glyph, shade: .45 + (1 - nearest / 3) * .55 })
    }
  }
  return cells
}

const shapes = [crystal(1), crystal(8), crystal(17)]

function crystalMask(shapeIndex: number, melt: number): string {
  const front = (1 - melt) * 1.1, wave = Math.sin(melt * Math.PI)
  let mask = ''
  for (const cell of shapes[shapeIndex]) {
    const radius = cell.radius + (cell.noise - .5) * .08 * wave
    if (radius > front) { mask += ' '; continue }
    const edge = front - radius
    let glyph = cell.glyph
    if (melt > 0 && edge < .055) glyph = '.'
    else if (melt > 0 && edge < .11) glyph = ':'
    mask += glyph
  }
  return mask
}

function paintCells(ctx: CanvasRenderingContext2D, shapeIndex: number, mask: string, opacity: number, color: string): void {
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
  ctx.font = '6px Menlo,monospace'
  ctx.fillStyle = color
  const cells = shapes[shapeIndex]
  for (let index = 0; index < cells.length; index++) {
    if (mask[index] === ' ') continue
    const cell = cells[index]
    ctx.globalAlpha = opacity * cell.shade
    ctx.fillText(mask[index], cell.x, cell.y)
    if (cell.noise > .8) {
      ctx.globalAlpha *= .2
      ctx.fillText('.', cell.x + 2, cell.y + 2)
    }
  }
}

export function drawCrystal(ctx: CanvasRenderingContext2D, shapeIndex: number, x: number, y: number, scale: number, angle: number, melt: number, opacity: number, color: string): void {
  if (melt >= 1) return
  ctx.save()
  ctx.translate(x, y); ctx.rotate(angle); ctx.scale(scale, scale)
  paintCells(ctx, shapeIndex, crystalMask(shapeIndex, melt), opacity, color)
  ctx.restore()
}

type CrystalSprite = { canvas: HTMLCanvasElement; mask: string; opacity: number }

export class CrystalCache {
  private sprites = new WeakMap<object, CrystalSprite>()
  private density = 0
  private color = ''

  configure(dpr: number, color: string): void {
    // Supersample the small glyphs so live rotation preserves fine crystal branches.
    const density = dpr * 2
    if (this.density === density && this.color === color) return
    this.density = density; this.color = color
    this.sprites = new WeakMap()
  }

  draw(ctx: CanvasRenderingContext2D, particle: object, shapeIndex: number, x: number, y: number, scale: number, angle: number, melt: number, opacity: number): void {
    if (melt >= 1) return
    // Cache the exact surviving glyphs, not rounded melt progress or animation frames.
    const mask = crystalMask(shapeIndex, melt)
    let sprite = this.sprites.get(particle)
    if (!sprite) {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = Math.ceil(100 * scale * this.density)
      sprite = { canvas, mask: '', opacity: -1 }
      this.sprites.set(particle, sprite)
    }
    const pixels = sprite.canvas.width
    if (sprite.mask !== mask || sprite.opacity !== opacity) {
      const raster = sprite.canvas.getContext('2d')!
      raster.setTransform(1, 0, 0, 1, 0, 0)
      raster.clearRect(0, 0, pixels, pixels)
      raster.setTransform(scale * this.density, 0, 0, scale * this.density, pixels / 2, pixels / 2)
      paintCells(raster, shapeIndex, mask, opacity, this.color)
      sprite.mask = mask; sprite.opacity = opacity
    }
    ctx.save()
    ctx.translate(x, y); ctx.rotate(angle)
    ctx.globalAlpha = 1
    const size = pixels / this.density
    ctx.drawImage(sprite.canvas, -size / 2, -size / 2, size, size)
    ctx.restore()
  }
}

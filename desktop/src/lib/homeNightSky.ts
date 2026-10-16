type Star = {
  x: number; y: number; r: number; a: number; start: number; life: number
  driftX: number; driftY: number; gold: boolean
}
type Meteor = {
  x: number; y: number; start: number; duration: number; travel: number; length: number; gold: boolean
}

// Approved homepage demo at 100% intensity. All coordinates cover the full canvas.
export function mountHomeNightSky(canvas: HTMLCanvasElement): () => void {
  const ctx = canvas.getContext('2d')!
  const root = document.documentElement
  const reduced = matchMedia('(prefers-reduced-motion: reduce)')
  let width = 0, height = 0, elapsed = 0, last = 0, frameId = 0, nextMeteor = .2, seed = 17
  let accent = '', ink = ''
  let meteors: Meteor[] = []
  function random(): number {
    seed = (seed * 16807) % 2147483647
    return (seed - 1) / 2147483646
  }
  function newStar(): Star {
    return {
      x: random(), y: random(), r: .4 + random() * .65, a: .25 + random() * .3,
      start: elapsed, life: 10 + random() * 12,
      driftX: (random() - .5) * 12, driftY: (random() - .5) * 6, gold: random() > .9,
    }
  }
  const stars = Array.from({ length: 120 }, () => {
    const star = newStar()
    return { ...star, start: -random() * star.life }
  })

  function paint(): void {
    ctx.clearRect(0, 0, width, height)
    if (root.classList.contains('light')) return
    const count = Math.min(stars.length, Math.round(width * height / 7500))
    for (const star of stars.slice(0, count)) {
      const age = Math.max(0, (elapsed - star.start) / star.life)
      const envelope = Math.sin(Math.min(1, age) * Math.PI) ** 2
      const x = star.x * width + star.driftX * age, y = star.y * height + star.driftY * age
      ctx.globalAlpha = star.a * (reduced.matches ? .55 : envelope)
      ctx.fillStyle = star.gold ? accent : ink
      ctx.beginPath()
      ctx.arc(x, y, star.r, 0, Math.PI * 2)
      ctx.fill()
    }
    if (!reduced.matches) for (const meteor of meteors) {
      const t = (elapsed - meteor.start) / meteor.duration
      if (t < 0 || t > 1) continue
      const x = meteor.x * width - t * width * meteor.travel
      const y = meteor.y * height + t * width * meteor.travel * .62
      const color = meteor.gold ? accent : ink
      const gradient = ctx.createLinearGradient(x, y, x + meteor.length, y - meteor.length * .62)
      gradient.addColorStop(0, color)
      gradient.addColorStop(.18, `color-mix(in srgb, ${color} 40%, transparent)`)
      gradient.addColorStop(1, 'transparent')
      ctx.globalAlpha = Math.sin(t * Math.PI) * .52
      ctx.strokeStyle = gradient
      ctx.lineWidth = meteor.gold ? 1.05 : .85
      ctx.beginPath()
      ctx.moveTo(x, y)
      ctx.lineTo(x + meteor.length, y - meteor.length * .62)
      ctx.stroke()
      ctx.fillStyle = color
      ctx.shadowColor = color
      ctx.shadowBlur = 3
      ctx.beginPath()
      ctx.arc(x, y, 1.1, 0, Math.PI * 2)
      ctx.fill()
      ctx.shadowBlur = 0
    }
    ctx.globalAlpha = 1
  }

  function frame(now: number): void {
    elapsed += Math.min((now - last) / 1000, .05)
    last = now
    if (elapsed > nextMeteor) {
      const count = elapsed % 12 < 2 ? 2 : 1
      for (let i = 0; i < count; i++) {
        const zone = (Math.floor(elapsed / 3) + i) % 3
        const x = .35 + random() * .75
        const position = random()
        let y = position * .22
        if (zone === 1) y = .25 + position * .25
        if (zone === 2) y = .7 + position * .15
        meteors.push({
          x, y, start: elapsed + i * .45, duration: 1.8 + random() * .5,
          travel: .28 + random() * .2, length: 65 + random() * 50, gold: random() > .85,
        })
      }
      nextMeteor = elapsed + 1.6 + random() * 1.4
    }
    for (let i = 0; i < stars.length; i++) {
      if (elapsed > stars[i].start + stars[i].life) stars[i] = newStar()
    }
    meteors = meteors.filter(meteor => elapsed < meteor.start + meteor.duration)
    paint()
    frameId = requestAnimationFrame(frame)
  }

  function sync(): void {
    cancelAnimationFrame(frameId)
    const style = getComputedStyle(root)
    accent = style.getPropertyValue('--color-accent').trim()
    ink = style.getPropertyValue('--color-ink').trim()
    paint()
    if (document.hidden || reduced.matches || root.classList.contains('light') || !width || !height) return
    last = performance.now()
    frameId = requestAnimationFrame(frame)
  }
  const resize = new ResizeObserver(() => {
    width = canvas.clientWidth
    height = canvas.clientHeight
    const dpr = Math.min(devicePixelRatio, 2)
    canvas.width = Math.round(width * dpr)
    canvas.height = Math.round(height * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    sync()
  })
  const theme = new MutationObserver(sync)
  resize.observe(canvas)
  theme.observe(root, { attributes: true, attributeFilter: ['class'] })
  document.addEventListener('visibilitychange', sync)
  reduced.addEventListener('change', sync)
  return () => {
    cancelAnimationFrame(frameId)
    resize.disconnect()
    theme.disconnect()
    document.removeEventListener('visibilitychange', sync)
    reduced.removeEventListener('change', sync)
  }
}

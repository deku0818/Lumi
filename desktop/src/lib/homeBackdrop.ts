import { NightSky } from './homeBackdrop/night'
import { SnowScene, type ComposerBounds } from './homeBackdrop/snow'

// Visual contract: .demos/ascii-home.html. The input and welcome text remain stationary.
export function mountHomeBackdrop(canvas: HTMLCanvasElement, detail: HTMLCanvasElement): () => void {
  const ctx = canvas.getContext('2d')!
  const foreground = detail.getContext('2d')!
  const root = document.documentElement
  const home = canvas.closest('.new-chat-home')!
  const composer = home.querySelector('.composer-glass')!
  const reduced = matchMedia('(prefers-reduced-motion: reduce)')
  const night = new NightSky()
  const snow = new SnowScene()
  let width = 0, height = 0, last = 0, frameId = 0, remainder = 0
  let snowTime = 1.2, nightTime = 1.2
  let light = false, dirty = true
  let inputBounds: ComposerBounds = { left: 0, right: 0, top: 0, bottom: 0 }
  const interval = 1 / 30

  function paint(): void {
    if (!width || !height) return
    ctx.clearRect(0, 0, width, height)
    foreground.clearRect(0, 0, width, height)
    if (light) {
      // Keep the accepted rim snow, but never paint over the input or its toolbar.
      foreground.save()
      foreground.beginPath()
      foreground.rect(0, 0, width, height)
      foreground.rect(inputBounds.left + 8, inputBounds.top + 8, Math.max(0, inputBounds.right - inputBounds.left - 16), Math.max(0, inputBounds.bottom - inputBounds.top - 16))
      foreground.clip('evenodd')
      snow.paint(ctx, foreground, width, height, snowTime, .85, reduced.matches)
      foreground.restore()
    } else night.paint(ctx, width, height, nightTime, .85, reduced.matches)
  }

  function frame(now: number): void {
    frameId = 0
    if (document.hidden) return
    const delta = Math.min((now - last) / 1000, .1)
    last = now
    const changed = dirty
    if (dirty) { measure(); dirty = false }
    if (!reduced.matches) {
      if (light) snowTime += delta
      else nightTime += delta
      remainder += delta
    }
    const due = remainder + 1e-7 >= interval
    if (due) remainder = Math.max(0, remainder - Math.floor((remainder + 1e-7) / interval) * interval)
    if (changed || due) paint()
    if (!reduced.matches && width && height) frameId = requestAnimationFrame(frame)
  }

  function measure(): void {
    const bounds = canvas.getBoundingClientRect()
    const input = composer.getBoundingClientRect()
    width = bounds.width; height = bounds.height
    inputBounds = { left: input.left - bounds.left, right: input.right - bounds.left, top: input.top - bounds.top, bottom: input.bottom - bounds.top }
    const dpr = Math.min(devicePixelRatio, 2)
    for (const target of [canvas, detail]) {
      if (target.width !== Math.round(width * dpr)) target.width = Math.round(width * dpr)
      if (target.height !== Math.round(height * dpr)) target.height = Math.round(height * dpr)
      target.getContext('2d')!.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    const style = getComputedStyle(root)
    const accent = style.getPropertyValue('--color-accent').trim()
    const info = style.getPropertyValue('--color-info').trim()
    light = root.classList.contains('light')
    night.palette(accent, style.getPropertyValue('--color-ink').trim(), info)
    if (width && height && light) {
      snow.resize(width, height, dpr, accent, style.getPropertyValue('--color-muted').trim(), info, inputBounds)
    }
  }

  function sync(): void {
    dirty = true
    if (document.hidden) {
      cancelAnimationFrame(frameId)
      frameId = 0
      remainder = 0
    } else if (!frameId) {
      last = performance.now()
      frameId = requestAnimationFrame(frame)
    }
  }

  const resize = new ResizeObserver(sync)
  const theme = new MutationObserver(sync)
  resize.observe(canvas)
  resize.observe(composer)
  theme.observe(root, { attributes: true, attributeFilter: ['class'] })
  document.addEventListener('visibilitychange', sync)
  window.addEventListener('resize', sync)
  reduced.addEventListener('change', sync)
  sync()
  return () => {
    cancelAnimationFrame(frameId)
    resize.disconnect()
    theme.disconnect()
    document.removeEventListener('visibilitychange', sync)
    window.removeEventListener('resize', sync)
    reduced.removeEventListener('change', sync)
  }
}

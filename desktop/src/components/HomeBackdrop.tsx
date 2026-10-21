import { useEffect, useRef, type JSX } from 'react'
import { mountHomeBackdrop } from '../lib/homeBackdrop'

export function HomeBackdrop(): JSX.Element {
  const canvas = useRef<HTMLCanvasElement>(null)
  const detail = useRef<HTMLCanvasElement>(null)
  useEffect(() => mountHomeBackdrop(canvas.current!, detail.current!), [])
  return (
    <div className="home-backdrop" aria-hidden="true">
      <canvas ref={canvas} className="home-backdrop-scene" />
      <canvas ref={detail} className="home-backdrop-detail" />
    </div>
  )
}

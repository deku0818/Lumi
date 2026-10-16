import { useEffect,useRef,type JSX } from 'react'
import { mountHomeNightSky } from '../lib/homeNightSky.ts'

export function HomeNightSky(): JSX.Element {
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => mountHomeNightSky(canvas.current!), [])
  return <canvas ref={canvas} className="home-night-sky" aria-hidden="true" />
}

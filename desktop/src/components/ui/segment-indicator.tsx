import { useLayoutEffect, useRef, useState } from 'react'

// 跟随真实按钮几何，兼容不同文字宽度、字体、语言和窗口尺寸。
export function SegmentIndicator() {
  const ref = useRef<HTMLSpanElement>(null)
  const [rect, setRect] = useState({ left: 0, width: 0 })
  useLayoutEffect(() => {
    const root = ref.current!.parentElement!
    const measure = () => {
      const active = root.querySelector<HTMLElement>('[aria-pressed="true"], [data-state="active"]')
      if (active) setRect(old => old.left === active.offsetLeft && old.width === active.offsetWidth
        ? old : { left: active.offsetLeft, width: active.offsetWidth })
    }
    measure()
    const resize = new ResizeObserver(measure)
    resize.observe(root)
    root.querySelectorAll('button').forEach(button => resize.observe(button))
    const mutation = new MutationObserver(measure)
    mutation.observe(root, { attributes: true, subtree: true, attributeFilter: ['aria-pressed', 'data-state'], childList: true })
    return () => { resize.disconnect(); mutation.disconnect() }
  }, [])
  return <span ref={ref} aria-hidden className="segment-indicator" style={{ ...rect, visibility: rect.width ? undefined : 'hidden' }} />
}

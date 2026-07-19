import { useEffect, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import { FLOAT_GAP } from '@/lib/utils'

// 边栏宽度：持久化到 localStorage，越界（含历史脏值）回退默认值。
export function useResizableWidth(key: string, def: number, min: number, max: number) {
  const [width, setRaw] = useState(() => {
    const v = Number(localStorage.getItem(key))
    return v >= min && v <= max ? v : def
  })
  useEffect(() => {
    localStorage.setItem(key, String(width))
  }, [key, width])
  // 设值器自带边界钳制，调用方无需再关心 min/max
  const setWidth = (w: number) => setRaw(Math.max(min, Math.min(max, w)))
  return { width, setWidth }
}

// 栏与主区之间的拖拽分隔条（作为 flex 兄弟节点）。
// edge='right'：把手在左侧栏右缘，右拖变宽；edge='left'：把手在右侧栏左缘，左拖变宽。
// 把手全程不可见（只靠 col-resize 光标提示）；拖拽期间 body.resizing-col 全局停用过渡，保证即时跟手。
export function ResizeHandle({
  width,
  setWidth,
  edge,
  floating = false,
}: {
  width: number
  setWidth: (w: number) => void
  edge: 'left' | 'right'
  // 悬浮面板场景：占位容器比面板宽 FLOAT_GAP，热区据此平移贴回面板可见边缘。
  // 方向由 edge 定，调用方无从传错（把手不可见，错位了也看不出来）
  floating?: boolean
}) {
  const shift = floating ? (edge === 'left' ? FLOAT_GAP : -FLOAT_GAP) : 0
  const onMouseDown = (e: ReactMouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = width
    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientX - startX
      setWidth(edge === 'right' ? startW + delta : startW - delta)
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.classList.remove('resizing-col')
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    document.body.classList.add('resizing-col')
  }
  return (
    <div
      onMouseDown={onMouseDown}
      style={shift ? { transform: `translateX(${shift}px)` } : undefined}
      className="shrink-0 w-1.5 -mx-0.5 z-10 cursor-col-resize"
    />
  )
}

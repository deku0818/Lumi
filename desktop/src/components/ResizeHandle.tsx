import { useEffect, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'

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
// 默认透明，hover 显示品牌金细线；拖拽期间 body.resizing-col 全局停用过渡，保证即时跟手。
export function ResizeHandle({
  width,
  setWidth,
  edge,
}: {
  width: number
  setWidth: (w: number) => void
  edge: 'left' | 'right'
}) {
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
      className="group shrink-0 w-1.5 -mx-0.5 z-10 cursor-col-resize flex justify-center"
    >
      <div className="w-px h-full bg-transparent group-hover:bg-primary/50 transition-colors" />
    </div>
  )
}

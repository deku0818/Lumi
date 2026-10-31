import type { ReactNode } from 'react'
import { cn, FLOAT_GAP, RAIL_EDGE_GAP } from '../lib/utils'
import { ResizeHandle } from './ResizeHandle'

// 保持内容挂载；侧栏和全窗口只改变同一个容器的尺寸。
export function WorkspaceWindow({ open, expanded, nativeTitleBar = false, width, setWidth, className, children }: {
  open: boolean
  expanded: boolean
  nativeTitleBar?: boolean
  width: number
  setWidth: (width: number) => void
  className?: string
  children: ReactNode
}) {
  const hidden = !open && !expanded
  return <div className={cn('workspace-window', expanded && nativeTitleBar && 'workspace-window-native-titlebar', className)}
    style={{
      width: expanded ? '100%' : width + RAIL_EDGE_GAP,
      padding: expanded ? 0 : `${FLOAT_GAP}px ${RAIL_EDGE_GAP}px ${FLOAT_GAP}px 0`,
      visibility: hidden ? 'hidden' : undefined,
    }} inert={hidden} aria-hidden={hidden}>
    {!expanded && <div className="workspace-window-resize"><ResizeHandle width={width} setWidth={setWidth} edge="left" /></div>}
    {children}
  </div>
}

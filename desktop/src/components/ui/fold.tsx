import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

// 内容常驻以保留手动展开状态；inert 阻止焦点进入收起的区域。
export function Fold({ open, children, className }: { open: boolean; children: ReactNode; className?: string }) {
  return (
    <span inert={!open} className={cn('lumi-fold grid', !open && 'lumi-fold-closed', className)}>
      <span className="block min-h-0 overflow-hidden">{children}</span>
    </span>
  )
}

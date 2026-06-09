// 居中模态外壳：半透明遮罩 + 模糊背景 + surface 卡片。审批/澄清/计划弹窗共用。
// maxWidth 用字面 Tailwind 类（如 'max-w-lg'）以便被静态扫描；className 追加内层布局。
import type { ReactNode } from 'react'

export function ModalShell({
  maxWidth = 'max-w-lg',
  className = '',
  children,
}: {
  maxWidth?: string
  className?: string
  children: ReactNode
}) {
  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6">
      <div className={`bg-surface border border-line rounded-2xl w-full p-5 shadow-2xl ${maxWidth} ${className}`}>
        {children}
      </div>
    </div>
  )
}

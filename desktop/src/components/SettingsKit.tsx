import { type ComponentProps, type ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

// 分段控件 / 步进器共用的药丸容器（SettingsDialog 的 stepper 也 import 复用，避免边框透明度漂移）。
export const segmentShell = 'inline-flex gap-0.5 p-0.5 rounded-lg bg-canvas/60 border border-line/40'

// 设置页统一排版原语：一处定义、四个面板共用，取代各面板各自造的 Row/Field/Seg/卡片。
// 目标是「一套 token 铺满全部面板」——标题字号、卡片、输入框、分段控件、间距只有一种写法。

// 分区：统一标题(13px/600) + 可选描述 + body，统一段间距（首个分区顶部不留白）。
// action 放标题右侧（如「添加」按钮）。
export function Section({
  title,
  desc,
  action,
  children,
  className,
}: {
  title?: ReactNode
  desc?: ReactNode
  action?: ReactNode
  children?: ReactNode
  className?: string
}) {
  // 段间距由 SectionGroup 的 space-y-7 提供（不依赖 :first-child，故 MachineTabs 在前也不会错位）。
  return (
    <section className={className}>
      {(title || action) && (
        <div className="flex items-center justify-between gap-3 mb-1">
          {title && (
            <h3 className="flex items-center gap-2 text-[13px] font-semibold text-ink">{title}</h3>
          )}
          {action}
        </div>
      )}
      {desc && <p className="text-xs text-muted-foreground leading-relaxed mb-3">{desc}</p>}
      {children}
    </section>
  )
}

// 分区之间的统一节奏容器：段间距只在这里定义一处（取代各面板各自写 space-y-7 magic number）。
export function SectionGroup({ children }: { children: ReactNode }) {
  return <div className="space-y-7">{children}</div>
}

// 横排：label 左 / 控件右（偏好类设置的默认布局）。
export function Row({
  label,
  hint,
  children,
}: {
  label: ReactNode
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-3 border-b border-line/20 last:border-b-0">
      <div className="min-w-0">
        <div className="text-[13px] text-ink">{label}</div>
        {hint && <div className="text-xs text-muted-foreground mt-0.5">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}

// 竖排字段：label 上 / 控件下（表单输入用）。
export function Field({
  label,
  hint,
  children,
}: {
  label: ReactNode
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <div>
      <div className="text-xs text-muted-foreground mb-1.5">{label}</div>
      {children}
      {hint && <div className="text-[11px] text-muted-foreground mt-1.5 leading-relaxed">{hint}</div>}
    </div>
  )
}

// 统一输入框样式（合并原三套 bg-canvas/60·bg-surface·bg-canvas 的分歧）。
export const inputClass =
  'w-full h-9 px-3 rounded-lg text-sm bg-canvas/50 text-ink border border-line/50 outline-none transition focus:border-primary/50 focus:ring-2 focus:ring-primary/15 placeholder:text-muted-foreground/50'

export function TextInput({
  password,
  type,
  className,
  ...props
}: ComponentProps<'input'> & { password?: boolean }) {
  // cn = clsx + tailwind-merge：调用方的 className 能正确覆盖 inputClass 里的冲突项（如 h-8 覆盖 h-9）。
  // type 透传：password 是语法糖，其余（text/time/number…）直接用调用方给的 type，故 time/number 也走本组件。
  return <input type={password ? 'password' : (type ?? 'text')} className={cn(inputClass, className)} {...props} />
}

// 统一卡片：透明描边 + 极淡填充 + 统一圆角/内边距。
export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn('rounded-xl border border-line/50 bg-surface/40 px-4 py-3', className)}>{children}</div>
}

// 统一分段控件（合并 SettingsDialog 的 Segmented 与 ChannelsPanel 的 Seg 两份实现）。
export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  className,
}: {
  value: T
  onChange: (v: T) => void
  options: { val: T; label?: ReactNode; icon?: LucideIcon; title?: string }[]
  className?: string
}) {
  return (
    <div className={cn(segmentShell, className)}>
      {options.map((o) => {
        const on = o.val === value
        return (
          <button
            key={o.val}
            onClick={() => onChange(o.val)}
            title={o.title}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-sm transition ${
              on ? 'bg-surface text-ink shadow-sm' : 'text-muted-foreground hover:text-ink'
            }`}
          >
            {o.icon && <o.icon size={15} className="shrink-0" />}
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

// 表单弹窗外壳：统一头(标题) / 体(可滚动) / 尾(操作条)。渠道、远程机器、Provider 三处编辑/添加共用。
// 约定「条件挂载即打开」——调用方用 {editing && <FormModal .../>} 控制显隐，故无需 open 属性。
// footer 内容由各表单自排（约定：左侧测试/次要操作，右侧取消/保存，用 <div className="flex-1" /> 撑开）。
export function FormModal({
  onClose,
  title,
  footer,
  children,
  className = 'sm:max-w-md',
  bodyClassName = 'max-h-[62vh]',
}: {
  onClose: () => void
  title: ReactNode
  footer?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent showCloseButton className={cn('p-0 gap-0 overflow-hidden', className)}>
        <DialogHeader className="px-5 pt-5 pb-3 border-b border-line/40">
          <DialogTitle className="text-sm">{title}</DialogTitle>
        </DialogHeader>
        <div className={cn('px-5 py-4 overflow-auto', bodyClassName)}>{children}</div>
        {footer && (
          <div className="px-5 py-3.5 border-t border-line/40 flex items-center gap-3">{footer}</div>
        )}
      </DialogContent>
    </Dialog>
  )
}

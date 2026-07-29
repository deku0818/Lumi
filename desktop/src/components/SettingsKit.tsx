import { type ComponentProps, type ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import type { CheckTone, EnvProgress } from '../types'
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

// 玻璃卡壳单一 token：Card / EntityCard / GroupCard / 渠道体检卡共用（描边与填充 alpha 只此一份）。
export const cardShell = 'rounded-xl border border-line/60 bg-surface/50'

// 统一卡片：透明描边 + 极淡填充 + 统一圆角/内边距。
export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn(cardShell, 'px-4 py-3', className)}>{children}</div>
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

// 安装进度光带（品牌金光晕语言）：percent 为 -1 时不可知（解压/npm 阶段），整条脉冲。
// 环境面板与渠道体检行共用——光效样式只此一份。
export function ProgressBar({ progress, className }: { progress: EnvProgress; className?: string }) {
  const known = progress.percent >= 0
  return (
    <div className={cn('flex items-center gap-2 text-[11px] text-muted-foreground', className)}>
      <span className="truncate">{progress.phase}</span>
      <span className="h-1 w-24 shrink-0 overflow-hidden rounded-full bg-separator/45">
        <i
          className={`block h-full rounded-full bg-primary shadow-[0_0_8px_var(--color-accent)] ${known ? 'transition-[width]' : 'w-full animate-pulse opacity-60'}`}
          style={known ? { width: `${progress.percent}%` } : undefined}
        />
      </span>
      {known && <span className="tabular-nums">{progress.percent}%</span>}
    </div>
  )
}

// ── 实体列表统一语法（.demos/settings-unify.html 定稿）──

// 状态点：全设置页统一 6px。连接/诊断语义带光晕，idle/hollow 为静态灰。
// title 用于悬停看详情（如 MCP「已连接 · N 个工具」）。语义色走 tone；机器色这类
// 动态色走 color（自带同色光晕）；pulse 独立叠加（连接中的金点/灰点呼吸都由它表达）。
export type StatusTone = CheckTone | 'idle' | 'hollow'
const DOT_TONE: Record<StatusTone, string> = {
  ok: 'bg-success shadow-[0_0_6px_var(--color-success)]',
  warn: 'bg-primary shadow-[0_0_6px_var(--color-accent)]',
  error: 'bg-error shadow-[0_0_6px_var(--color-error)]',
  idle: 'bg-separator',
  hollow: 'border-[1.5px] border-separator opacity-70',
}
export function StatusDot({
  tone,
  color,
  pulse,
  title,
  className,
}: {
  tone?: StatusTone
  color?: string // CSS 颜色值，优先于 tone
  pulse?: boolean
  title?: string
  className?: string
}) {
  return (
    <span
      title={title}
      className={cn(
        'size-1.5 rounded-full shrink-0',
        !color && tone && DOT_TONE[tone],
        pulse && 'animate-pulse',
        className,
      )}
      style={color ? { background: color, boxShadow: `0 0 6px ${color}` } : undefined}
    />
  )
}

// 胶囊徽章一族：transport tag / 工具来源 / 「即将支持」共用。
// dot 金点=Lumi 托管、蓝点=系统来源；dashed=缺失/未来时；tag=大写小标签（stdio/HTTP/IM）。
export function Pill({
  dot,
  dashed,
  tag,
  children,
}: {
  dot?: 'gold' | 'info'
  dashed?: boolean
  tag?: boolean
  children: ReactNode
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-px text-[10.5px] text-muted-foreground shrink-0',
        dashed ? 'border-dashed border-separator' : dot === 'gold' ? 'border-primary/45 text-ink' : 'border-separator',
        tag && 'px-1.5 text-[10px] font-medium uppercase tracking-wide',
      )}
    >
      {/* 私有小点（5px/5px 光晕）：比 StatusDot 小一号是刻意的胶囊内比例，不共用 */}
      {dot && (
        <i
          className={cn(
            'size-[5px] rounded-full shrink-0',
            dot === 'gold' ? 'bg-primary shadow-[0_0_5px_var(--color-accent)]' : 'bg-info',
          )}
        />
      )}
      {children}
    </span>
  )
}

// 实体行卡：渠道 / MCP server / 模型供应商 / 远程机器共用的一套解剖，次序恒定：
// chip 36px → 标题行(名称 + meta：Pill/状态点/状态字) → 副题 → 徽章常显
// → 操作图标 hover 浮现 → Switch 恒最右（竖向对齐成列，扫一眼全局开关状态）。
// 截断由本组件恒管：title 是名称本体（自动 truncate），meta 放不参与截断的附属件；
// subtitleTitle 给被截断的副题（地址/命令）一个悬停看全文的出口。
export function EntityCard({
  icon,
  title,
  meta,
  subtitle,
  subtitleTitle,
  badge,
  actions,
  trailing,
  dim,
}: {
  icon: ReactNode
  title: ReactNode
  meta?: ReactNode
  subtitle?: ReactNode
  subtitleTitle?: string
  badge?: ReactNode
  actions?: ReactNode
  trailing?: ReactNode
  dim?: boolean
}) {
  return (
    <div className={cn(cardShell, 'group flex items-center gap-3 px-3.5 py-2.5', dim && 'opacity-55')}>
      <div className="grid place-items-center w-9 h-9 rounded-lg bg-surface border border-line text-ink shrink-0">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex min-w-0 items-center gap-2 font-medium">
          <span className="truncate">{title}</span>
          {meta}
        </div>
        {subtitle && (
          <div title={subtitleTitle} className="text-[11px] text-muted-foreground mt-0.5 truncate">
            {subtitle}
          </div>
        )}
      </div>
      {badge}
      {/* 未悬停时不仅隐形还要不可命中：opacity-0 仍可点会让卡片右缘藏一颗隐形删除键 */}
      {actions && (
        <div className="flex items-center opacity-0 pointer-events-none transition-opacity group-hover:opacity-100 group-hover:pointer-events-auto focus-within:opacity-100 focus-within:pointer-events-auto">
          {actions}
        </div>
      )}
      {trailing}
    </div>
  )
}

// 表单分组卡：头行(图标 chip + 标题 + 副题 + 尾控件) + hairline + 内容。
// 开关型分组把 Switch 传 action、open 随开关——开合语言与体检卡一致。
export function GroupCard({
  icon: Icon,
  title,
  desc,
  action,
  open = true,
  bodyClassName,
  children,
}: {
  icon: LucideIcon
  title: ReactNode
  desc?: ReactNode
  action?: ReactNode
  open?: boolean
  bodyClassName?: string
  children?: ReactNode
}) {
  return (
    <div className={cn(cardShell, 'overflow-hidden')}>
      <div className="flex items-center gap-3 px-4 py-3">
        <div className="grid place-items-center w-7 h-7 rounded-lg bg-surface border border-line/60 text-muted-foreground shrink-0">
          <Icon size={15} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[12.5px] font-semibold">{title}</div>
          {desc && <div className="text-[11px] text-muted-foreground mt-0.5">{desc}</div>}
        </div>
        {action}
      </div>
      {/* bodyClassName 替换布局默认（space-y-4）而非叠加——网格布局的调用方不必写 space-y-0 反削 */}
      {open && children && (
        <div className={cn('px-4 pb-4 pt-3 border-t border-line/40', bodyClassName ?? 'space-y-4')}>{children}</div>
      )}
    </div>
  )
}

// 空态统一虚线框：一句现状 + 一句去处。
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-separator px-5 py-7 text-center text-[12px] leading-relaxed text-muted-foreground">
      {children}
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

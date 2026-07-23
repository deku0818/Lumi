// 统一右栏：收放钮 + 模块卡竖排。模块（执行记录 / 后台任务…）以 RailSection 往下叠，
// 定时任务会话置顶「执行记录」，其余模块两种会话通用。开合状态由 App 持有并持久化。
import { useState } from 'react'
import { ChevronDown, PanelRight } from 'lucide-react'
import { useI18n } from '../i18n'
import { Button } from '@/components/ui/button'
import { FLOAT_GAP } from '@/lib/utils'

// 面板右缘到窗口边的缝：比通用 FLOAT_GAP 收紧（Cowork 式贴边）
const RAIL_EDGE_GAP = 4

export function RightRail({
  width,
  open,
  onToggle,
  dot,
  enter,
  children,
}: {
  width: number
  open: boolean
  onToggle: () => void
  dot?: boolean // 收起态在钮上亮脉冲点（有任务在跑）
  enter?: boolean // 挂载时播放入场动画（宽度展开+面板滑入）：聊天视图首个后台任务出现用
  children: React.ReactNode
}) {
  const { t } = useI18n()
  return (
    <aside
      style={{ width: open ? width + RAIL_EDGE_GAP : 0 }}
      className={`relative shrink-0 transition-[width] duration-300 ease-out ${enter ? 'rail-enter' : ''}`}
    >
      {/* 收放钮：浮在聊天区右上角（Cowork 式，锚在栏左缘外侧 -left-10 = 钮 28 + 缝 12，
          缝里让出聊天区滚动条），栏收起（宽 0）后同一锚点贴近窗口右缘（滚动条在钮右侧，
          同 Cowork）。top-[13px] 让钮心与左侧栏收起钮 / mac 红绿灯中线同线（y=27）；
          这会探进 mac 顶条
          拖拽带（36px），故需 titlebar-interactive（no-drag 挖洞 + macOS26 命中合成层
          修复，同 Sidebar 面板内按钮的先例；drag 区域按布局盒计算，宽度动画期间钮正
          处于淡入隐身期，静止后洞是稳的）。样式与左侧两颗收放钮完全同款（ghost/icon-sm、
          无 aria-expanded——ghost 变体的 aria-expanded:bg-muted 会让展开态常亮底色）。
          open 翻转时交替 toggle-fade-in 变体重触发动画（见 index.css）：面板滑动期间
          隐身，滑完在新位置原地淡入 */}
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={onToggle}
        title={open ? t('sidebar.collapse') : t('sidebar.expand')}
        className={`${open ? 'toggle-fade-in' : 'toggle-fade-in-alt'} titlebar-interactive absolute -left-10 top-[13px] z-10 text-muted-foreground hover:text-ink`}
      >
        <PanelRight />
        {dot && !open && (
          <span className="absolute top-0.5 right-0.5 size-1.5 rounded-full bg-primary animate-pulse" />
        )}
      </Button>
      {/* 模块卡竖排：各卡自然高度，整列滚动；收起时整列滑出窗口右缘
          （110% 与左侧栏的 -110% 镜像，滑不干净会在聊天区上方留淡出残影）后 invisible
          掐掉不可见动画的合成开销（visibility 离散过渡，滑完才翻转，不打断滑出）；
          inert 防 Tab 进不可见区 */}
      <div
        inert={!open}
        style={{ width, right: RAIL_EDGE_GAP, top: FLOAT_GAP, bottom: FLOAT_GAP }}
        className={`rail-panel absolute flex flex-col gap-2.5 overflow-y-auto transition-[translate,opacity,visibility] duration-300 ease-out ${
          open ? '' : 'translate-x-[110%] opacity-0 invisible'
        }`}
      >
        {children}
      </div>
    </aside>
  )
}

// 右栏模块卡：玻璃材质 + 节头（标题 / 计数 / 附加操作 / chevron），点节头独立折叠。
// 节头用 div 而非 button：headerExtra 里可放操作按钮（如「清除已完成」）。
// grid-rows 0fr↔1fr：能给"内容自适应高度"做过渡的唯一干净写法；
// inert：折叠后内容只是被裁到 0 高，不加这个仍能 Tab 进去触发交互。
export function RailSection({
  title,
  count,
  headerExtra,
  children,
}: {
  title: string
  count?: React.ReactNode // 数字或「5 · 2 运行中」这类摘要串
  headerExtra?: React.ReactNode
  children: React.ReactNode
}) {
  const [collapsed, setCollapsed] = useState(false)
  const toggle = () => setCollapsed((c) => !c)
  return (
    <section className="sidebar-float rounded-panel shrink-0 overflow-hidden">
      {/* div 化的节头要自己补齐 button 的键盘语义：tabIndex 可聚焦 + Enter/Space 触发 */}
      <div
        role="button"
        tabIndex={0}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            toggle()
          }
        }}
        aria-expanded={!collapsed}
        className="flex items-center gap-1.5 px-3 py-2.5 cursor-pointer select-none transition hover:bg-ink/[0.04]"
      >
        <span className="text-[13.5px] font-semibold">{title}</span>
        {count !== undefined && <span className="text-xs text-muted-foreground">{count}</span>}
        <span className="ml-auto flex items-center gap-1">
          {headerExtra}
          <ChevronDown
            className={`size-4 shrink-0 text-muted-foreground transition-transform duration-300 ease-[cubic-bezier(.32,.72,0,1)] ${
              collapsed ? '-rotate-90' : ''
            }`}
          />
        </span>
      </div>
      {/* 外层只负责裁剪（0fr 时把内层 padding 一起收掉，否则折叠后残留一条空底） */}
      <div
        inert={collapsed}
        className={`grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(.32,.72,0,1)] ${
          collapsed ? 'grid-rows-[0fr] opacity-0' : 'grid-rows-[1fr]'
        }`}
      >
        <div className="overflow-hidden">
          <div className="px-2.5 pb-2.5">{children}</div>
        </div>
      </div>
    </section>
  )
}

import { useEffect, useRef } from 'react'
import type { SlashCommand } from '../types'

// 斜杠命令补全菜单：悬浮在 composer 上方，键盘上下选择 + Enter/Tab 确认。
// 选中态由父组件（App）持有，键盘事件也在 composer 的 textarea 上统一处理。
export function CommandMenu({
  commands,
  selected,
  onPick,
  onHover,
}: {
  commands: SlashCommand[]
  selected: number
  onPick: (cmd: SlashCommand) => void
  onHover: (index: number) => void
}) {
  const ref = useRef<HTMLDivElement>(null)

  // 选中项滚动进可视区（键盘导航越过 viewport 时）
  useEffect(() => {
    ref.current?.children[selected]?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  return (
    <div className="mb-1.5 rounded-2xl border border-line/40 bg-surface shadow-lg overflow-hidden">
      <div ref={ref} className="max-h-64 overflow-auto py-1">
        {commands.map((cmd, i) => (
          <button
            key={cmd.name}
            onMouseDown={(e) => {
              e.preventDefault() // 保持 textarea 焦点
              onPick(cmd)
            }}
            onMouseEnter={() => onHover(i)}
            className={`w-full flex items-baseline gap-3 px-4 py-1.5 text-left transition-colors ${
              i === selected ? 'bg-primary/10' : 'hover:bg-white/5'
            }`}
          >
            <span className="text-primary font-medium shrink-0">/{cmd.name}</span>
            <span className="text-sm text-muted truncate">{cmd.description}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

// 「先选机器」选择条：多机时渲染一排机器 pill；单机自然退化为 null（无需调用方各自判断）。
// ProjectsPage / CronPage / ProvidersPanel 共用，消除三份逐字相同的 chip markup。
export function MachineTabs({
  machines,
  value,
  onChange,
  className = 'flex flex-wrap gap-2 mb-4',
}: {
  machines: { id: string; name: string; enabled?: boolean }[]
  value: string
  onChange: (id: string) => void
  className?: string
}) {
  // 已关闭（不连接）的机器不出 pill
  const shown = machines.filter((m) => m.enabled !== false)
  if (shown.length <= 1) return null
  return (
    <div className={className}>
      {shown.map((m) => (
        <button
          key={m.id}
          onClick={() => onChange(m.id)}
          className={`px-3 py-1.5 rounded-lg text-sm border transition ${
            m.id === value
              ? 'border-primary/50 bg-primary/10 text-ink'
              : 'border-line text-muted-foreground hover:text-ink hover:border-separator'
          }`}
        >
          {m.name}
        </button>
      ))}
    </div>
  )
}

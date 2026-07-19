import { useEffect, useState } from 'react'
import { Bot, Boxes, Check, ChevronDown, ChevronRight, Square, SquareTerminal, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { BgTask, BgTaskKind, BgTaskProgress } from '../types'
import { useI18n } from '../i18n'
import { CARD_L2, FLOAT_GAP } from '@/lib/utils'

const isTerminal = (t: BgTask): boolean => t.status !== 'running'

// 后台任务右栏（Cowork 式）：一摞可独立折叠的任务卡片；开关在头部 PanelRight 图标（由 App 管）。
// 后端数据见 TaskRegistry（serialize_task）；实时刷新经 bg_tasks.update 事件。

const KIND_ICON: Record<BgTaskKind, LucideIcon> = {
  workflow: Boxes,
  agent: Bot,
  bash: SquareTerminal,
}

const displayName = (t: BgTask): string =>
  t.agent_name || t.label.replace(/^(workflow|agent|bash):/, '')

const duration = (t: BgTask): string => {
  const end = t.completed_at ?? Date.now() / 1000
  return `${Math.max(0, Math.round(end - t.started_at))}s`
}

const statusLabel = (t: BgTask, tr: (k: string) => string): string =>
  t.status === 'running'
    ? tr('bg.running')
    : t.status === 'completed'
      ? tr('bg.completed')
      : t.status === 'timed_out'
        ? tr('bg.timedOut')
        : tr('bg.failed')

// 折叠时的一行摘要：kind + 进度/退出码/状态
const hintLine = (t: BgTask, tr: (k: string) => string): string => {
  if (t.kind === 'workflow' && t.progress?.total != null)
    return `workflow · ${t.progress.done ?? 0}/${t.progress.total}`
  if (t.kind === 'bash' && t.exit_code != null) return `bash · ${tr('bg.exitCode')} ${t.exit_code}`
  return `${t.kind} · ${statusLabel(t, tr)}`
}

// 运行中默认展开（关注正在跑的），其余默认折叠
const defaultCollapsed = (t: BgTask): boolean => t.status !== 'running'

function StatusMark({ t }: { t: BgTask }) {
  if (t.status === 'running') return <span className="lumi-orb" />
  if (t.status === 'completed')
    return <Check size={14} className={t.kind === 'bash' ? 'text-success' : 'text-primary'} />
  return <span className="text-error text-xs font-bold leading-none">✕</span>
}

function WorkflowProgress({ p }: { p: BgTaskProgress }) {
  const { t } = useI18n()
  const pct = p.total ? Math.round(((p.done ?? 0) / p.total) * 100) : 0
  return (
    <div className="mt-1">
      {p.phase && (
        <div className="text-[11px] text-muted-foreground mb-1.5">
          {p.phase}
          {p.total != null ? ` · ${p.done ?? 0}/${p.total}` : ''}
          {p.running ? ` · ${p.running} ${t('bg.running')}` : ''}
        </div>
      )}
      {p.total != null ? (
        <div className="h-1.5 rounded-full bg-ink/10 overflow-hidden">
          <div
            className="h-full bg-primary rounded-full transition-[width] duration-700"
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : null}
    </div>
  )
}

function TaskCard({
  task,
  onStop,
  onDismiss,
  collapsed,
  onToggle,
}: {
  task: BgTask
  onStop: (taskId: string) => void
  onDismiss: (taskId: string) => void
  collapsed: boolean
  onToggle: () => void
}) {
  const { t } = useI18n()
  const Icon = KIND_ICON[task.kind]
  const running = task.status === 'running'
  const terminal = isTerminal(task)
  return (
    <div className={`group ${CARD_L2} overflow-hidden`}>
      {/* 卡头是可点的折叠开关（div 而非 button，以容纳内部的移除 button） */}
      <div
        onClick={onToggle}
        className="w-full flex items-center gap-2.5 px-3.5 py-3 text-left cursor-pointer hover:bg-white/[0.03]"
      >
        <Icon size={16} className="text-muted-foreground shrink-0" />
        <span
          className={`font-semibold flex-1 min-w-0 truncate ${task.kind === 'bash' ? 'font-mono text-[13px]' : ''}`}
        >
          {displayName(task)}
        </span>
        <StatusMark t={task} />
        {/* 终态：hover 显示灰色移除 ✕（与红色状态 ✕ 区分：位置在最右、灰色、仅 hover）；
            出现时让位 chevron */}
        {terminal ? (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onDismiss(task.task_id)
            }}
            title={t('bg.dismiss')}
            className="hidden group-hover:grid place-items-center w-5 h-5 rounded text-muted-foreground hover:text-ink hover:bg-white/10 shrink-0"
          >
            <X size={13} />
          </button>
        ) : null}
        <ChevronDown
          size={14}
          className={`text-muted-foreground transition-transform shrink-0 ${collapsed ? '-rotate-90' : ''} ${terminal ? 'group-hover:hidden' : ''}`}
        />
      </div>
      {collapsed ? (
        <div className="px-3.5 pb-3 -mt-1 text-xs text-muted-foreground truncate">
          {hintLine(task, t)}
        </div>
      ) : (
        <div className="px-3.5 pb-3.5">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground text-xs mb-2.5">
            <span>{statusLabel(task, t)}</span>
            <span>
              {t('bg.duration')} <b className="text-ink font-medium">{duration(task)}</b>
            </span>
            {task.exit_code != null && (
              <span>
                {t('bg.exitCode')}{' '}
                <b className={task.exit_code === 0 ? 'text-success' : 'text-error'}>
                  {task.exit_code}
                </b>
              </span>
            )}
            {task.agent_count != null && (
              <span>
                <b className="text-ink font-medium">{task.agent_count}</b> {t('bg.subagents')}
              </span>
            )}
          </div>
          {task.error && <div className="text-error text-xs mb-2 break-words">{task.error}</div>}
          {task.kind === 'workflow' && task.progress && <WorkflowProgress p={task.progress} />}
          <div className="text-[11px] text-muted-foreground/60 mt-2.5 break-all selectable">
            {task.output_file}
          </div>
          {running && (
            <button
              onClick={() => onStop(task.task_id)}
              className="mt-2.5 inline-flex items-center gap-1.5 text-error border border-error/40 rounded-lg px-2.5 py-1 text-xs hover:bg-error/10"
            >
              <Square size={11} fill="currentColor" /> {t('bg.stop')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export function BgTasksDrawer({
  tasks,
  onStop,
  onDismiss,
  onClearFinished,
  onClose,
  open,
  width,
}: {
  tasks: BgTask[]
  onStop: (taskId: string) => void
  onDismiss: (taskId: string) => void
  onClearFinished: () => void
  onClose: () => void
  open: boolean
  width: number
}) {
  const { t } = useI18n()
  // 用户手动折叠/展开覆盖（无记录则用 defaultCollapsed）
  const [override, setOverride] = useState<Record<string, boolean>>({})
  // 每秒 tick：运行中任务的 duration 实时跳动（仅面板打开且有任务在跑时计时，省开销）
  const [, setTick] = useState(0)
  const running = tasks.filter((x) => x.status === 'running').length
  useEffect(() => {
    if (!open || running === 0) return
    const id = setInterval(() => setTick((x) => x + 1), 1000)
    return () => clearInterval(id)
  }, [open, running])
  if (tasks.length === 0) return null
  const finished = tasks.length - running

  return (
    <aside
      style={{ width: open ? width + FLOAT_GAP * 2 : 0 }}
      className={`relative shrink-0 transition-[width] duration-200 ${open ? '' : 'pointer-events-none'}`}
    >
      {/* 悬浮玻璃面板：与左侧栏对称。开合时面板向右滑出/滑入（transform+opacity），
          与外层宽度动画同步——面板绝对定位不受宽度裁剪，缺了这层过渡会整块瞬间弹出 */}
      <div
        style={{ width, right: FLOAT_GAP, top: FLOAT_GAP, bottom: FLOAT_GAP }}
        className={`absolute sidebar-float rounded-panel overflow-hidden flex flex-col transition-[transform,opacity] duration-200 ease-out ${
          open ? '' : 'translate-x-[110%] opacity-0'
        }`}
      >
        {/* 标题行与执行记录栏同款字号；收起只由右侧 chevron 触发（标题是纯文本，
            避免整条空白都成了误触关闭的热区），chevron 平时隐身、hover 才现 */}
        <div className="group shrink-0 flex items-center gap-2 pl-3 pr-2 py-2.5">
          <span className="text-[13.5px] font-semibold">{t('bg.title')}</span>
          {running > 0 && (
            <span className="text-[11px] text-muted-foreground tabular-nums">
              {running} {t('bg.running')}
            </span>
          )}
          {finished > 0 && (
            <button
              onClick={onClearFinished}
              className="text-[11px] text-muted-foreground hover:text-ink hover:bg-ink/5 rounded px-1.5 py-0.5"
            >
              {t('bg.clearFinished')} {finished}
            </button>
          )}
          <button
            onClick={onClose}
            title={t('common.close')}
            aria-label={t('common.close')}
            className="ml-auto shrink-0 text-muted-foreground opacity-0 transition-opacity duration-200 group-hover:opacity-100 hover:text-ink"
          >
            <ChevronRight className="size-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-2.5 pb-2.5 flex flex-col gap-3">
          {tasks.map((task) => {
            const collapsed = override[task.task_id] ?? defaultCollapsed(task)
            return (
              <TaskCard
                key={task.task_id}
                task={task}
                onStop={onStop}
                onDismiss={onDismiss}
                collapsed={collapsed}
                onToggle={() => setOverride((o) => ({ ...o, [task.task_id]: !collapsed }))}
              />
            )
          })}
        </div>
      </div>
    </aside>
  )
}

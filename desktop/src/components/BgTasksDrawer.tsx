import { memo, useEffect, useRef, useState } from 'react'
import {
  Bot,
  Boxes,
  Check,
  ChevronDown,
  ChevronRight,
  Square,
  SquareTerminal,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { BgTask, BgTaskKind, BgTaskOutput, BgTaskProgress } from '../types'
import { EMPTY_BG_OUTPUT } from '../types'
import { useI18n } from '../i18n'
import { RailSection } from './RightRail'
import { Button } from '@/components/ui/button'
import { CARD_L2, fmtSize } from '@/lib/utils'

const isTerminal = (t: BgTask): boolean => t.status !== 'running'

// 后台任务模块（挂在统一右栏 RightRail 里）：一摞可独立折叠的任务卡片。
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
    <div className="mb-2.5">
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
      {/* 脚本 log() 的最新一条：输出文件完成时才写，运行中只有它能说明「跑到哪了」 */}
      {p.last_log && (
        <div className="mt-1.5 text-[10.5px] font-mono text-muted-foreground truncate">
          ▸ {p.last_log}
        </div>
      )}
    </div>
  )
}

// 后台 agent 运行中的活动行：正在调用的工具（模型思考中时为空）+ 已完成工具数
function AgentActivity({ p }: { p: BgTaskProgress }) {
  const { t } = useI18n()
  return (
    <div className="flex items-center gap-2 text-[11.5px] mb-2.5">
      <span className="lumi-orb scale-[0.65] -mx-0.5" />
      {p.tool ? (
        <>
          {t('bg.calling')} <span className="font-mono text-[11px] text-primary">{p.tool}</span>
        </>
      ) : (
        <span className="text-muted-foreground">{t('bg.thinking')}</span>
      )}
      {p.tools_done ? (
        <span className="text-muted-foreground">· {t('bg.toolsUsed', { n: p.tools_done })}</span>
      ) : null}
    </div>
  )
}

// 任务内容：bash 是完整命令（等宽块，卡头只放得下一行截断），其余是交给它的 prompt。
// 两者同一规则——默认 clamp 三行，超出才给展开出口（长命令能撑十几行，会把输出预览挤出视野）。
function TaskIntent({ task }: { task: BgTask }) {
  const { t } = useI18n()
  const [expanded, setExpanded] = useState(false)
  const [overflow, setOverflow] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const mono = task.kind === 'bash'
  const text = mono ? task.label : task.prompt
  // 只随文本量一次：展开后 scrollHeight 等于 clientHeight，再量会把展开出口自己量没
  useEffect(() => {
    const el = ref.current
    if (el) setOverflow(el.scrollHeight > el.clientHeight + 1)
  }, [text])

  if (!text) return null
  return (
    <div className="mb-2.5">
      {!mono && (
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          {t('bg.intent')}
        </div>
      )}
      <div
        ref={ref}
        onClick={() => overflow && setExpanded((v) => !v)}
        className={`selectable text-ink/80 ${expanded ? '' : 'line-clamp-3'} ${
          mono
            ? 'rounded-md bg-ink/[0.05] px-2 py-1.5 font-mono text-[11px] leading-relaxed break-all'
            : 'text-[11.5px] leading-relaxed'
        } ${overflow ? 'cursor-pointer' : ''}`}
      >
        {text}
      </div>
      {overflow && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-[10.5px] text-muted-foreground hover:text-ink"
        >
          {expanded ? `▴ ${t('common.showLess')}` : `▾ ${t('bg.showAll')}`}
        </button>
      )}
    </div>
  )
}

// 输出文件尾部：卡片可见时拉一次，边跑边写的任务（bash）每 2s 续拉。
// ``active`` 必须把右栏开合算进去：栏收起时卡片只是被移出视口/裁到 0 高，组件仍挂着，
// 不看这个的话几个运行中的任务会在没人看的时候持续轮询（同下方每秒计时的 open 门）。
function useTaskOutput(task: BgTask, active: boolean, read: (id: string) => Promise<BgTaskOutput>) {
  const [tail, setTail] = useState<BgTaskOutput>(EMPTY_BG_OUTPUT)
  // 序号守卫跨 effect 轮次有效：只认最后发出的那次，先发后到不会把新尾巴覆盖成旧的
  const seq = useRef(0)
  // running 必须留在依赖里：任务转终态时重跑本 effect 才会去取那份「完成时才写」的结果
  const running = task.status === 'running'
  useEffect(() => {
    if (!active) return
    const pull = () => {
      const mine = ++seq.current
      void read(task.task_id)
        .then((r) => mine === seq.current && setTail(r))
        .catch(() => {})
    }
    pull()
    // 只有边跑边写的任务值得续拉；agent / workflow 运行中输出文件恒空，轮询纯属空转
    if (!running || !task.streams_output) return
    const id = setInterval(pull, 2000)
    return () => clearInterval(id)
  }, [active, running, task.streams_output, task.task_id, read])
  return tail
}

// 输出预览：运行中贴底流出最后几行（顶部渐隐，同子代理工具窗口的语言），
// 终态收成一行「查看输出」按需展开成可滚动的等宽框——终态输出常常很长，默认摊开会淹掉卡片
function OutputPreview({ task, tail }: { task: BgTask; tail: BgTaskOutput }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const lines = tail.text ? tail.text.replace(/\n$/, '').split('\n') : []
  const mono = 'font-mono text-[10.5px] leading-[1.65] whitespace-pre'

  if (task.status === 'running') {
    // 空态不占位：边写边跑的任务给一行「等待输出…」，其余（输出到完成才写）什么都不画——
    // 上方的活动行 / 进度条已经说明它在做什么
    if (!lines.length)
      return task.streams_output ? (
        <div className="text-[11px] text-muted-foreground/70">{t('bg.waitingOutput')}</div>
      ) : null
    return (
      <div
        className={`${mono} h-[52px] flex flex-col justify-end overflow-hidden text-ink/60 [mask-image:linear-gradient(to_bottom,transparent_0,#000_20px)]`}
      >
        {lines.slice(-3).map((l, i) => (
          <div key={i} className="truncate">
            {l}
          </div>
        ))}
      </div>
    )
  }

  if (!lines.length) return null
  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-ink py-0.5"
      >
        <ChevronRight size={12} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
        {open ? t('bg.hideOutput') : `${t('bg.viewOutput')} · ${lines.length}`}
      </button>
      {open && (
        <div
          className={`${mono} mt-1 max-h-56 overflow-auto rounded-md border border-line/50 bg-canvas/60 px-2 py-1.5 text-ink/70 selectable`}
        >
          {/* 后端只回末 8KB：不标出来的话，一个 5MB 的构建日志看着就像"这就是全部输出" */}
          {tail.truncated && (
            <div className="text-muted-foreground/70 font-sans text-[10.5px] mb-1">
              {t('bg.outputTruncated', { total: fmtSize(tail.size) })}
            </div>
          )}
          {lines.map((l, i) => (
            <div key={i}>{l}</div>
          ))}
        </div>
      )}
    </div>
  )
}

function TaskCard({
  task,
  onStop,
  onDismiss,
  onReadOutput,
  open,
  collapsed,
  onToggle,
}: {
  task: BgTask
  onStop: (taskId: string) => void
  onDismiss: (taskId: string) => void
  onReadOutput: (taskId: string) => Promise<BgTaskOutput>
  open: boolean
  collapsed: boolean
  onToggle: () => void
}) {
  const { t } = useI18n()
  const Icon = KIND_ICON[task.kind]
  const running = task.status === 'running'
  const terminal = isTerminal(task)
  // 右栏收起时卡片仍挂载着，必须一并当作不可见，否则没人看也在每 2s 轮询
  const tail = useTaskOutput(task, open && !collapsed, onReadOutput)
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
          <TaskIntent task={task} />
          {task.kind === 'workflow' && task.progress && <WorkflowProgress p={task.progress} />}
          {task.kind === 'agent' && running && task.progress && (
            <AgentActivity p={task.progress} />
          )}
          <OutputPreview task={task} tail={tail} />
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

// memo：App 流式期间每 token 重渲染，props 全稳定（tasks 是 useMemo、回调是 useCallback），
// 有任务在跑时这里是常驻子树，不 memo 就白陪跑
export const BgTasksSection = memo(function BgTasksSection({
  tasks,
  open,
  onStop,
  onDismiss,
  onReadOutput,
  onClearFinished,
}: {
  tasks: BgTask[]
  open: boolean // 右栏开合：收起时停表，省掉隐藏子树的每秒重渲染
  onStop: (taskId: string) => void
  onDismiss: (taskId: string) => void
  onReadOutput: (taskId: string) => Promise<BgTaskOutput>
  onClearFinished: () => void
}) {
  const { t } = useI18n()
  // 用户手动折叠/展开覆盖（无记录则用 defaultCollapsed）
  const [override, setOverride] = useState<Record<string, boolean>>({})
  // 每秒 tick：运行中任务的 duration 实时跳动（仅右栏展开且有任务在跑时计时，省开销）
  const [, setTick] = useState(0)
  const running = tasks.filter((x) => x.status === 'running').length
  useEffect(() => {
    if (!open || running === 0) return
    const id = setInterval(() => setTick((x) => x + 1), 1000)
    return () => clearInterval(id)
  }, [open, running])
  const finished = tasks.length - running

  return (
    <RailSection
      title={t('bg.title')}
      // 总数之外保留旧头部的「N 运行中」：运行/完成占比一眼可见，不必逐卡辨认转圈
      count={running > 0 ? `${tasks.length} · ${running} ${t('bg.running')}` : tasks.length}
      headerExtra={
        finished > 0 ? (
          <Button
            variant="ghost"
            size="xs"
            onClick={(e) => {
              e.stopPropagation() // 节头是折叠开关，别让清除顺手把节折了
              onClearFinished()
            }}
            className="text-[11px] font-normal text-muted-foreground hover:text-ink"
          >
            {t('bg.clearFinished')} {finished}
          </Button>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-2.5">
        {tasks.map((task) => {
          const collapsed = override[task.task_id] ?? defaultCollapsed(task)
          return (
            <TaskCard
              key={task.task_id}
              task={task}
              onStop={onStop}
              onDismiss={onDismiss}
              onReadOutput={onReadOutput}
              open={open}
              collapsed={collapsed}
              onToggle={() => setOverride((o) => ({ ...o, [task.task_id]: !collapsed }))}
            />
          )
        })}
      </div>
    </RailSection>
  )
})

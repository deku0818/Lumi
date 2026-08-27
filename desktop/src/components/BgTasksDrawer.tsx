import { memo, useEffect, useRef, useState } from 'react'
import { Bot, Boxes, Check, ChevronDown, Square, SquareTerminal, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { BgTask, BgTaskKind, BgTaskOutput } from '../types'
import { EMPTY_BG_OUTPUT } from '../types'
import { useI18n } from '../i18n'
import type { Translate } from '../i18n'
import { RailSection } from './RightRail'
import { Button } from '@/components/ui/button'
import { CARD_L2, fmtSize } from '@/lib/utils'

// 后台任务模块（挂在统一右栏 RightRail 里）：运行中的一摞紧凑卡片 + 已完成折叠成一行。
// 分组是刻意的——终态任务每会话可攒到 20 条（后端 _TERMINAL_CAP），平铺会把正在跑的
// 挤出视野。后端数据见 TaskRegistry（serialize_task）；实时刷新经 bg_tasks.update 事件。

const KIND_ICON: Record<BgTaskKind, LucideIcon> = {
  workflow: Boxes,
  agent: Bot,
  bash: SquareTerminal,
}

// 元信息行的第一格。三种 kind 都是专名，不进 i18n
const KIND_LABEL: Record<BgTaskKind, string> = {
  workflow: 'Workflow',
  agent: 'Agent',
  bash: 'Bash',
}

const displayName = (t: BgTask): string =>
  t.agent_name || t.label.replace(/^(workflow|agent|bash):/, '')

const duration = (t: BgTask): string => {
  const end = t.completed_at ?? Date.now() / 1000
  return `${Math.max(0, Math.round(end - t.started_at))}s`
}

const statusLabel = (t: BgTask, tr: Translate): string =>
  t.status === 'running'
    ? tr('bg.running')
    : t.status === 'completed'
      ? tr('bg.completed')
      : t.status === 'timed_out'
        ? tr('bg.timedOut')
        : tr('bg.failed')

// 元信息行的第三格「这一刻在干什么」：workflow=阶段进度 / agent=当前工具 / bash=退出码。
// 空串即不占格（agent 刚起步、bash 还在跑时都没什么可说的）
const activity = (task: BgTask, tr: Translate): string => {
  const p = task.progress
  if (task.kind === 'workflow' && p?.total != null)
    return `${p.phase ?? ''} ${p.done ?? 0}/${p.total}`.trim()
  if (task.kind === 'agent' && task.status === 'running')
    return p?.tool ? `${tr('bg.calling')} ${p.tool}` : tr('bg.thinking')
  if (task.kind === 'bash' && task.exit_code != null)
    return `${tr('bg.exitCode')} ${task.exit_code}`
  return ''
}

const outputLines = (tail: BgTaskOutput): string[] =>
  tail.text ? tail.text.replace(/\n$/, '').split('\n') : []

// 展开开关是 div（内部要塞停止 / 移除 button，button 不能嵌 button），
// 键盘语义得自己补齐——同 RailSection 节头的先例
const toggleProps = (onToggle: () => void, expanded: boolean) => ({
  role: 'button',
  tabIndex: 0,
  'aria-expanded': expanded,
  onClick: onToggle,
  onKeyDown: (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onToggle()
    }
  },
})

function StatusMark({ t }: { t: BgTask }) {
  const { t: tr } = useI18n()
  const title = statusLabel(t, tr)
  if (t.status === 'running') return <span className="lumi-orb" title={title} />
  if (t.status === 'completed')
    return (
      <Check
        size={13}
        className={t.kind === 'bash' ? 'text-success' : 'text-primary'}
        aria-label={title}
      />
    )
  return (
    <span className="text-error text-xs font-bold leading-none" title={title}>
      ✕
    </span>
  )
}

// 输出文件尾部：需要显示时拉一次，边跑边写的任务（bash）每 2s 续拉。
// ``active`` 必须把右栏开合与卡片是否显示输出一起算进去：栏收起时卡片只是被移出视口，
// 组件仍挂着，不看这个的话几个运行中的任务会在没人看的时候持续轮询（同下方每秒计时的 open 门）。
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

// 展开后的详情：任务内容（bash 是命令原文）+ 完整输出尾部 + 错误。
// 不做行数 clamp——本身已在一次点击之后，再加一层「展开全部」是多余的门；改用定高滚动
function TaskDetail({ task, tail }: { task: BgTask; tail: BgTaskOutput }) {
  const { t } = useI18n()
  const mono = task.kind === 'bash'
  const text = mono ? task.label : task.prompt
  const lines = outputLines(tail)
  return (
    <div className="mt-2 pt-2 border-t border-line/40">
      {task.exit_code != null && (
        <div className="text-[11px] text-muted-foreground mb-1.5">
          {t('bg.exitCode')}{' '}
          <b className={task.exit_code === 0 ? 'text-success' : 'text-error'}>{task.exit_code}</b>
        </div>
      )}
      {task.error && (
        <div className="text-error text-[11.5px] mb-1.5 break-words">{task.error}</div>
      )}
      {text && (
        <>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            {mono ? t('bg.command') : t('bg.intent')}
          </div>
          <div
            className={`selectable max-h-40 overflow-auto leading-relaxed text-ink/80 ${
              mono
                ? 'rounded-md bg-ink/[0.05] px-2 py-1.5 font-mono text-[11px] break-all'
                : 'text-[11.5px]'
            }`}
          >
            {text}
          </div>
        </>
      )}
      {lines.length > 0 && (
        <div className="mt-2 max-h-44 overflow-auto rounded-md border border-line/50 bg-canvas/60 px-2 py-1.5 font-mono text-[10.5px] leading-[1.65] whitespace-pre text-ink/70 selectable">
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
      {task.agent_count != null && (
        <div className="mt-1.5 text-[11px] text-muted-foreground">
          <b className="text-ink font-medium">{task.agent_count}</b> {t('bg.subagents')}
        </div>
      )}
    </div>
  )
}

// 运行中的卡片：图标 + 名字 + 光点 + 常驻停止钮 / 元信息一行 / 进度条 / 最新一行动静。
// 停止不再藏在展开之后——正在跑的任务最常做的操作就是叫停它
function RunningCard({
  task,
  tail,
  onStop,
  expanded,
  onToggle,
}: {
  task: BgTask
  tail: BgTaskOutput
  onStop: (taskId: string) => void
  expanded: boolean
  onToggle: () => void
}) {
  const { t } = useI18n()
  const Icon = KIND_ICON[task.kind]
  const p = task.progress
  const pct = task.kind === 'workflow' && p?.total ? Math.round(((p.done ?? 0) / p.total) * 100) : null
  const act = activity(task, t)
  const lines = outputLines(tail)
  // 卡片上只留一行动静：workflow 是脚本 log()，bash 是输出末行（还没吐字就说明在等）
  const lastLine =
    task.kind === 'workflow'
      ? p?.last_log
      : task.streams_output
        ? (lines[lines.length - 1] ?? t('bg.waitingOutput'))
        : null

  return (
    <div className={`${CARD_L2} px-2.5 py-2`}>
      {/* 点头部区域展开详情；详情在这块之外，免得选中里面的文字顺手把卡片收了 */}
      <div {...toggleProps(onToggle, expanded)} className="cursor-pointer">
        <div className="flex items-center gap-2">
          <Icon size={14} className="text-muted-foreground shrink-0" />
          <span
            className={`flex-1 min-w-0 truncate font-semibold ${
              task.kind === 'bash' ? 'font-mono text-[11.5px]' : 'text-[12.5px]'
            }`}
          >
            {displayName(task)}
          </span>
          <span className="lumi-orb scale-[0.85]" />
          <button
            onClick={(e) => {
              e.stopPropagation()
              onStop(task.task_id)
            }}
            title={t('bg.stop')}
            className="grid place-items-center size-5 shrink-0 rounded-md text-muted-foreground hover:text-error hover:bg-error/15"
          >
            <Square size={11} fill="currentColor" />
          </button>
        </div>
        {/* 缩进对齐名字：图标 14 + gap 8。分隔点跟着后一格走（同 span 内不换行），
            否则窄栏换行时会在行尾留一个孤零零的「·」 */}
        <div className="mt-1 pl-[22px] flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] text-muted-foreground">
          <span>{KIND_LABEL[task.kind]}</span>
          <b className="font-medium text-ink/85 tabular-nums whitespace-nowrap">
            <span className="opacity-45 font-normal">· </span>
            {duration(task)}
          </b>
          {act && (
            <span className="min-w-0 truncate">
              <span className="opacity-45">· </span>
              {act}
            </span>
          )}
          {task.kind === 'agent' && p?.tools_done ? (
            <span className="whitespace-nowrap">
              <span className="opacity-45">· </span>
              {t('bg.toolsUsed', { n: p.tools_done })}
            </span>
          ) : null}
        </div>
        {pct !== null && (
          <div className="mt-1.5 ml-[22px] h-[3px] rounded-full bg-ink/10 overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-[width] duration-700"
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
        {lastLine && (
          <div className="mt-1.5 pl-[22px] font-mono text-[10.5px] text-muted-foreground/80 truncate">
            {lastLine}
          </div>
        )}
      </div>
      {expanded && <TaskDetail task={task} tail={tail} />}
    </div>
  )
}

// 已完成的一行：名字 + 结果标记 + 用时，点开就地出详情，hover 出移除 ✕
function FinishedRow({
  task,
  tail,
  onDismiss,
  expanded,
  onToggle,
}: {
  task: BgTask
  tail: BgTaskOutput
  onDismiss: (taskId: string) => void
  expanded: boolean
  onToggle: () => void
}) {
  const { t } = useI18n()
  const Icon = KIND_ICON[task.kind]
  return (
    <div className={expanded ? `${CARD_L2} px-2.5 py-2` : ''}>
      <div
        {...toggleProps(onToggle, expanded)}
        className={`group flex items-center gap-2 cursor-pointer rounded-lg ${
          expanded ? '' : 'px-2 py-1.5 hover:bg-white/[0.04]'
        }`}
      >
        <Icon size={13} className="text-muted-foreground shrink-0" />
        <span
          className={`flex-1 min-w-0 truncate text-ink/80 ${
            task.kind === 'bash' ? 'font-mono text-[11px]' : 'text-[12px]'
          }`}
        >
          {displayName(task)}
        </span>
        <StatusMark t={task} />
        <span className="text-[11px] text-muted-foreground tabular-nums">{duration(task)}</span>
        {/* 恒占位、hover 才显形：否则 ✕ 一出现会把用时整行往左挤 */}
        <button
          onClick={(e) => {
            e.stopPropagation()
            onDismiss(task.task_id)
          }}
          title={t('bg.dismiss')}
          className="grid place-items-center size-4 shrink-0 rounded text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-ink"
        >
          <X size={12} />
        </button>
      </div>
      {expanded && <TaskDetail task={task} tail={tail} />}
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
  // 详情单开：右栏只有 256 宽，两份详情同时摊开就得来回滚
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [finOpen, setFinOpen] = useState(false)
  // 每秒 tick：运行中任务的 duration 实时跳动（仅右栏展开且有任务在跑时计时，省开销）
  const [, setTick] = useState(0)
  const running = tasks.filter((x) => x.status === 'running')
  const finished = tasks.filter((x) => x.status !== 'running')
  useEffect(() => {
    if (!open || running.length === 0) return
    const id = setInterval(() => setTick((x) => x + 1), 1000)
    return () => clearInterval(id)
  }, [open, running.length])

  const toggle = (id: string) => setExpandedId((cur) => (cur === id ? null : id))

  return (
    <RailSection
      title={t('bg.title')}
      count={running.length > 0 ? `${running.length} ${t('bg.running')}` : tasks.length}
    >
      <div className="flex flex-col gap-2">
        {running.map((task) => (
          <TaskSlot
            key={task.task_id}
            task={task}
            open={open}
            expanded={expandedId === task.task_id}
            onToggle={() => toggle(task.task_id)}
            onStop={onStop}
            onDismiss={onDismiss}
            onReadOutput={onReadOutput}
          />
        ))}
        {finished.length > 0 && (
          <div className={running.length > 0 ? 'border-t border-line/40 pt-1' : ''}>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setFinOpen((v) => !v)}
                className="flex-1 flex items-center gap-1.5 px-1.5 py-1 text-[11.5px] text-muted-foreground hover:text-ink"
              >
                <ChevronDown
                  size={13}
                  className={`transition-transform duration-300 ease-[cubic-bezier(.32,.72,0,1)] ${finOpen ? '' : '-rotate-90'}`}
                />
                {t('bg.completed')} {finished.length}
              </button>
              <Button
                variant="ghost"
                size="xs"
                onClick={onClearFinished}
                className="text-[11px] font-normal text-muted-foreground hover:text-ink"
              >
                {t('bg.clear')}
              </Button>
            </div>
            {finOpen && (
              <div className="flex flex-col gap-0.5 mt-0.5">
                {finished.map((task) => (
                  <TaskSlot
                    key={task.task_id}
                    task={task}
                    open={open}
                    expanded={expandedId === task.task_id}
                    onToggle={() => toggle(task.task_id)}
                    onStop={onStop}
                    onDismiss={onDismiss}
                    onReadOutput={onReadOutput}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </RailSection>
  )
})

// 取输出的那层壳：hook 只能在组件里调，运行中 / 已完成两种行都要它
function TaskSlot({
  task,
  open,
  expanded,
  onToggle,
  onStop,
  onDismiss,
  onReadOutput,
}: {
  task: BgTask
  open: boolean
  expanded: boolean
  onToggle: () => void
  onStop: (taskId: string) => void
  onDismiss: (taskId: string) => void
  onReadOutput: (taskId: string) => Promise<BgTaskOutput>
}) {
  const running = task.status === 'running'
  // 要么详情摊开了，要么是边跑边写、卡片上那行动静得实时跟着走；其余情况一概不取
  const tail = useTaskOutput(task, open && (expanded || (running && task.streams_output)), onReadOutput)
  return running ? (
    <RunningCard task={task} tail={tail} onStop={onStop} expanded={expanded} onToggle={onToggle} />
  ) : (
    <FinishedRow
      task={task}
      tail={tail}
      onDismiss={onDismiss}
      expanded={expanded}
      onToggle={onToggle}
    />
  )
}

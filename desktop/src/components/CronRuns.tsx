import { AlertTriangle, ChevronRight, Info, Square } from 'lucide-react'
import { Popover } from 'radix-ui'
import { memo } from 'react'
import type { CronActiveRun, CronRun } from '../types'
import { useI18n } from '../i18n'
import { cn, fmtDuration } from '@/lib/utils'
import { CARD_L3, POPOVER } from './glass'
import { Button } from './ui/button'
import { CopyButton } from './chat/MessageActions'
import { RailSection } from './RightRail'
import { useCronRuns, type CronRunEntry, type CronRunsApi } from '../hooks/useCronRuns'

const RUN_STATUS = {
  success: { labelKey: 'cron.tabRuns', Icon: Info, className: 'text-muted-foreground' },
  failed: { labelKey: 'cron.runFailed', Icon: AlertTriangle, className: 'text-error' },
  timeout: { labelKey: 'cron.runTimeout', Icon: AlertTriangle, className: 'text-primary' },
  stopped: { labelKey: 'cron.runStopped', Icon: Square, className: 'text-muted-foreground fill-current' },
}

function RunDetails({ run }: { run: CronRun }) {
  const { t } = useI18n()
  const { labelKey, Icon, className } = RUN_STATUS[run.status]
  const label = t(labelKey)
  const text = run.status === 'success' ? run.output_summary : run.error || label
  if (run.status === 'success' && (run.thread_id || !text)) return null
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button variant="ghost" size="icon-sm" className="shrink-0 mr-2" title={label} aria-label={t('cron.runDetails', { status: label })}>
          <Icon className={cn('size-3', className)} />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="bottom" align="end" sideOffset={8} aria-label={label} className={cn(POPOVER, 'z-50 w-[min(340px,calc(100vw-32px))]')}>
          <div className="flex items-center justify-between px-2 pb-1 text-xs font-medium">
            <span>{label}</span><CopyButton text={text} />
          </div>
          <div className="selectable max-h-56 overflow-y-auto px-2 pb-2 text-xs leading-relaxed whitespace-pre-wrap break-words">{text}</div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

// 两处列表共用同一行：时间 / 状态 / 入口。错误详情独立于导航按钮，不影响行高。
function RunCard({ run, active, unread, onPick }: {
  run: CronRunEntry
  active: boolean
  unread: boolean
  onPick: (threadId: string) => void
}) {
  const { t, lang } = useI18n()
  const done = 'status' in run ? run : null
  const date = new Date(run.started_at)
  const time = date.toLocaleTimeString(lang === 'zh' ? 'zh-CN' : 'en-US', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
  const day = date.toLocaleDateString(lang === 'zh' ? 'zh-CN' : 'en-US', {
    ...(date.getFullYear() === new Date().getFullYear() ? {} : { year: 'numeric' }), month: 'short', day: 'numeric',
  })
  return (
    <div className={cn(CARD_L3, 'flex items-center text-left transition', active && 'border-primary/45 bg-primary/[0.08]', !run.thread_id && 'opacity-60')}>
      <button disabled={!run.thread_id} onClick={() => onPick(run.thread_id)} className="group flex flex-1 min-w-0 items-center gap-2.5 px-3 py-2.5 text-left rounded-lg hover:bg-surface/70 disabled:cursor-default">
        <span className="flex-1 min-w-0">
          <span className="block text-sm font-semibold tabular-nums leading-tight">{time}</span>
          <span className="block text-[11px] text-muted-foreground truncate mt-0.5">{day}</span>
        </span>
        {done ? (
          <span className="text-[11px] text-muted-foreground tabular-nums shrink-0">{fmtDuration(Math.round(done.duration_ms / 1000))}</span>
        ) : (
          <><span className="lumi-orb shrink-0" aria-hidden="true" /><span className="text-xs text-primary shrink-0">{t('cron.runningBadge')}</span></>
        )}
        {done?.status === 'success' && unread && <span className="size-1.5 rounded-full bg-info shrink-0" />}
        {run.thread_id && <ChevronRight size={14} className="shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />}
      </button>
      {done && <RunDetails run={done} />}
    </div>
  )
}

export function RunRecords({ entries, activeThread, readRuns, onPick }: {
  entries: CronRunEntry[] | null
  activeThread?: string | null
  readRuns?: Record<string, true>
  onPick: (threadId: string) => void
}) {
  const { t } = useI18n()
  if (entries === null) return null
  return (
    <div className="flex flex-col gap-2.5">
      {entries.length === 0 && <div className="py-1 text-sm text-muted-foreground">{t('cron.noRuns')}</div>}
      {entries.map(run => <RunCard key={run.thread_id || run.started_at} run={run} active={!!run.thread_id && run.thread_id === activeThread} unread={!!readRuns && !!run.thread_id && !readRuns[run.thread_id]} onPick={onPick} />)}
    </div>
  )
}

// 只有右栏拥有节标题、计数和开合；取数 hook 与记录内容和详情页共用。
export const RunsSection = memo(function RunsSection({ api, machine, jobId, version, machineRuns, open, activeThread, readRuns, onPick }: {
  api: () => CronRunsApi | undefined
  machine: string
  jobId: string
  version: number
  machineRuns?: CronActiveRun[]
  open: boolean
  activeThread: string | null
  readRuns: Record<string, true>
  onPick: (threadId: string) => void
}) {
  const { t } = useI18n()
  const entries = useCronRuns(api, machine, jobId, version, machineRuns, open)
  return (
    <RailSection title={t('cron.tabRuns')} count={entries?.length}>
      <RunRecords entries={entries} activeThread={activeThread} readRuns={readRuns} onPick={onPick} />
    </RailSection>
  )
})

// 定时任务管理整页视图（参考 Cowork Scheduled tasks）。
// 列表页：标题 + 新建按钮 → 信息条幅 → 任务卡片网格；
// 详情页：返回链接 → 大标题 + 编辑/删除/立即运行 → 开关 + 状态 + 下次运行
//        → 左右两栏「执行记录 | 任务内容」。
// 数据经 gateway 的 cron RPC 读写；cron.result/cron.running 事件由 App 转为
// version/runningJobs props 驱动刷新，本组件不直接订阅 WS。
import { memo, useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Clock,
  Info,
  Loader2,
  Pencil,
  Play,
  Plus,
  Trash2,
} from 'lucide-react'
import type { CronJob, CronRun } from '../types'
import { useI18n, type Translate } from '../i18n'
import { ConfirmDialog } from './ConfirmDialog'
import { MachineTabs } from './MachineTabs'
import { RailSection } from './RightRail'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import { CARD_L2, beOf, cn, errorMessage } from '@/lib/utils'

// 后端能力句柄：App 注入 anyGw() 返回的 Gateway 子集，便于解耦与测试
export interface CronApi {
  listCronJobs(): Promise<{ jobs: CronJob[] }>
  createCronJob(name: string, schedule: string, prompt: string): Promise<{ job: CronJob }>
  updateCronJob(
    jobId: string,
    fields: { name?: string; schedule?: string; prompt?: string },
  ): Promise<{ job: CronJob }>
  deleteCronJob(jobId: string): Promise<{ job_id: string }>
  toggleCronJob(jobId: string, enabled: boolean): Promise<{ job: CronJob }>
  runCronJob(jobId: string): Promise<{ ok: boolean }>
  listCronRuns(jobId: string, limit?: number): Promise<{ runs: CronRun[] }>
}

const pad = (n: number) => String(n).padStart(2, '0')

const fmtTime = (iso: string) => {
  const d = new Date(iso)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// 执行记录卡片用：时刻为主、日期为次，分开返回。同年省略年份（6月8日 / Jun 8）。
const runParts = (iso: string, lang: string) => {
  const d = new Date(iso)
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const sameYear = d.getFullYear() === new Date().getFullYear()
  const date = d.toLocaleDateString(lang === 'zh' ? 'zh-CN' : 'en-US', {
    ...(sameYear ? {} : { year: 'numeric' }),
    month: 'short',
    day: 'numeric',
  })
  return { date, time }
}

// 调度规则人类可读化：覆盖常见形态（间隔简写、每天/每周 cron、一次性），
// 其余 cron 表达式按原文显示（足够看懂，不重造 cron 解析器）。
function describeSchedule(job: CronJob, t: Translate): string {
  const { type, value } = job.schedule
  if (type === 'interval') {
    const m = /^(\d+)([smhd])$/.exec(value)
    if (m) {
      const key = { s: 'cron.everySecond', m: 'cron.everyMinute', h: 'cron.everyHour', d: 'cron.everyDay' }[m[2]]!
      return t(key, { n: m[1] })
    }
    return value
  }
  if (type === 'at') return t('cron.once', { time: fmtTime(value) })
  // cron：仅识别「分 时 * * *」和「分 时 * * 周几」两种最常见形态
  const m = /^(\d{1,2}) (\d{1,2}) \* \* (\*|\d)$/.exec(value)
  if (m) {
    const time = `${pad(Number(m[2]))}:${pad(Number(m[1]))}`
    if (m[3] === '*') return t('cron.dailyAt', { time })
    const day = t('cron.weekdays').split(',')[Number(m[3]) % 7]
    return t('cron.weeklyAt', { day, time })
  }
  return value
}

// 调度状态徽章（启用=描述调度规则，停用=「已暂停」），JobCard 与 JobDetail 共用
function ScheduleBadge({ job }: { job: CronJob }) {
  const { t } = useI18n()
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs ${
        job.enabled ? 'bg-success/10 text-success' : 'bg-line/30 text-muted-foreground'
      }`}
    >
      <Clock size={11} />
      {job.enabled ? describeSchedule(job, t) : t('cron.paused')}
    </span>
  )
}

export function CronPage({
  api,
  machines,
  jobs,
  runningJobs,
  version,
  onOpenRun,
  onRefresh,
}: {
  api: (backend: string) => CronApi | undefined // 按机器取连接（定时是 per-机器）
  machines: { id: string; name: string }[]
  jobs: CronJob[] // App 持有的跨机器合并列表（带 backend 标记）
  runningJobs: Record<string, string[]> // 机器 → 该机器运行中的 job id
  version: number
  onOpenRun: (threadId: string, jobId: string) => void
  onRefresh: () => void
}) {
  const { t } = useI18n()
  // 方案甲「先选机器」：定时任务在各自机器的调度器上跑，按机器管理。
  const [machine, setMachine] = useState('local')
  const [selectedId, setSelectedId] = useState<string | null>(null) // 详情页任务
  const [dialog, setDialog] = useState<{ job: CronJob | null } | null>(null) // 创建/编辑表单
  const [pendingDelete, setPendingDelete] = useState<CronJob | null>(null)

  const gw = api(machine)
  // 传给子组件：操作落在选中机器。稳定引用——否则每次渲染换新身份会让 useCronRuns 反复重拉。
  const boundApi = useCallback(() => gw, [gw])
  const shownJobs = jobs.filter((j) => (j.backend || 'local') === machine)
  const pickMachine = (m: string) => {
    setSelectedId(null)
    setMachine(m)
  }

  const toggle = (job: CronJob, enabled: boolean) => {
    gw?.toggleCronJob(job.id, enabled).then(onRefresh).catch(onRefresh)
  }

  const runNow = (job: CronJob) => {
    gw?.runCronJob(job.id).catch(() => {})
  }

  const doDelete = (job: CronJob) => {
    setPendingDelete(null)
    if (selectedId === job.id) setSelectedId(null)
    gw?.deleteCronJob(job.id).then(onRefresh).catch(() => {})
  }

  const selected = selectedId ? shownJobs.find((j) => j.id === selectedId) : undefined

  return (
    <div className="flex-1 overflow-auto">
      {selected ? (
        <JobDetail
          api={boundApi}
          job={selected}
          running={(runningJobs[beOf(selected)] ?? []).includes(selected.id)}
          version={version}
          onBack={() => setSelectedId(null)}
          onEdit={() => setDialog({ job: selected })}
          onDelete={() => setPendingDelete(selected)}
          onToggle={(v) => toggle(selected, v)}
          onRunNow={() => runNow(selected)}
          onOpenRun={onOpenRun}
        />
      ) : (
        <div className="max-w-4xl mx-auto w-full px-8 py-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="serif text-2xl">{t('cron.title')}</h1>
              <p className="text-sm text-muted-foreground mt-1">{t('cron.subtitle')}</p>
            </div>
            <Button onClick={() => setDialog({ job: null })} className="shrink-0 rounded-xl gap-1.5">
              <Plus size={15} />
              {t('cron.new')}
            </Button>
          </div>

          <MachineTabs
            machines={machines}
            value={machine}
            onChange={pickMachine}
            className="mt-4 flex flex-wrap gap-2"
          />

          <div className="mt-5 flex items-center gap-2.5 rounded-2xl border border-line/30 bg-surface/60 px-4 py-3 text-sm text-muted-foreground">
            <Info size={15} className="shrink-0 text-info" />
            {t('cron.banner')}
          </div>

          {shownJobs.length === 0 ? (
            <div className="mt-16 flex flex-col items-center text-center select-none">
              <Clock size={36} className="text-muted-foreground/50" />
              <div className="mt-4 text-ink">{t('cron.empty')}</div>
              <div className="mt-1 text-sm text-muted-foreground">{t('cron.emptyHint')}</div>
            </div>
          ) : (
            <div className="mt-5 grid gap-4 grid-cols-[repeat(auto-fill,minmax(230px,1fr))]">
              {shownJobs.map((job) => (
                <JobCard
                  key={job.id}
                  job={job}
                  running={(runningJobs[beOf(job)] ?? []).includes(job.id)}
                  onOpen={() => setSelectedId(job.id)}
                  onToggle={(v) => toggle(job, v)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {dialog && (
        <JobFormDialog
          api={boundApi}
          job={dialog.job}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null)
            onRefresh()
          }}
        />
      )}
      {pendingDelete && (
        <ConfirmDialog
          title={t('cron.deleteTitle')}
          message={t('cron.deleteMessage', { name: pendingDelete.name })}
          onConfirm={() => doDelete(pendingDelete)}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  )
}

// 列表页卡片：名称 + prompt 摘要 + 调度 chip + 启用开关。点击进入详情。
function JobCard({
  job,
  running,
  onOpen,
  onToggle,
}: {
  job: CronJob
  running: boolean
  onOpen: () => void
  onToggle: (enabled: boolean) => void
}) {
  const { t } = useI18n()
  return (
    <div
      className={`flex flex-col rounded-2xl border border-line/30 bg-surface/40 hover:bg-surface/70 hover:border-line/60 transition cursor-pointer p-4 ${job.enabled ? '' : 'opacity-55'}`}
      onClick={onOpen}
    >
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0 font-medium truncate">{job.name}</div>
        {running && <Loader2 size={15} className="shrink-0 mt-0.5 animate-spin text-primary" />}
        {!running && job.consecutive_errors > 0 && (
          <span title={t('cron.errorsHint', { n: job.consecutive_errors })} className="shrink-0 mt-0.5">
            <AlertTriangle size={15} className="text-primary" />
          </span>
        )}
      </div>

      <div className="mt-1.5 text-sm text-muted-foreground line-clamp-3 flex-1">{job.prompt}</div>

      <div className="mt-3.5 flex items-center justify-between gap-2">
        <ScheduleBadge job={job} />
        <span onClick={(e) => e.stopPropagation()} className="shrink-0">
          <Switch checked={job.enabled} onCheckedChange={onToggle} />
        </span>
      </div>
    </div>
  )
}

// 详情页：参考 Cowork 任务详情 —— 返回 → 标题/操作 → 状态行 → History | Instructions
function JobDetail({
  api,
  job,
  running,
  version,
  onBack,
  onEdit,
  onDelete,
  onToggle,
  onRunNow,
  onOpenRun,
}: {
  api: () => CronApi | undefined
  job: CronJob
  running: boolean
  version: number
  onBack: () => void
  onEdit: () => void
  onDelete: () => void
  onToggle: (enabled: boolean) => void
  onRunNow: () => void
  onOpenRun: (threadId: string, jobId: string) => void
}) {
  const { t } = useI18n()
  return (
    <div className="max-w-4xl mx-auto w-full px-8 py-6">
      <button
        onClick={onBack}
        className="flex items-center gap-1 text-sm text-muted-foreground hover:text-ink transition -ml-1"
      >
        <ChevronLeft size={16} />
        {t('cron.back')}
      </button>

      <div className="mt-5 flex items-start justify-between gap-4">
        <h1 className="serif text-3xl min-w-0 break-words">{job.name}</h1>
        <div className="shrink-0 flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" onClick={onEdit} aria-label={t('cron.editTitle')} className="text-muted-foreground">
            <Pencil />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={onDelete} aria-label={t('cron.deleteTitle')} className="text-muted-foreground">
            <Trash2 />
          </Button>
          <Button onClick={onRunNow} disabled={running} className="ml-2 rounded-xl gap-1.5">
            {running ? <Loader2 size={15} className="animate-spin" /> : <Play size={14} />}
            {running ? t('cron.runningBadge') : t('cron.runNow')}
          </Button>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-3 flex-wrap">
        <Switch checked={job.enabled} onCheckedChange={onToggle} />
        <ScheduleBadge job={job} />
        {job.enabled && job.next_run && (
          <span className="text-sm text-muted-foreground">{t('cron.nextRun', { time: fmtTime(job.next_run) })}</span>
        )}
      </div>

      {job.consecutive_errors > 0 && (
        <div className="mt-5 flex items-center gap-2.5 rounded-2xl border border-primary/40 bg-primary/10 px-4 py-3 text-sm">
          <AlertTriangle size={15} className="shrink-0 text-primary" />
          {t('cron.errorsHint', { n: job.consecutive_errors })}
        </div>
      )}

      <div className="mt-8 pt-6 border-t border-line/30 grid grid-cols-1 md:grid-cols-[5fr_7fr] gap-10">
        <div>
          <div className="text-sm text-muted-foreground mb-2">{t('cron.tabRuns')}</div>
          <RunList
            api={api}
            jobId={job.id}
            version={version}
            onOpenRun={(tid) => onOpenRun(tid, job.id)}
          />
        </div>
        <div>
          <div className="text-sm text-muted-foreground mb-2">{t('cron.prompt')}</div>
          <div className="selectable text-[15px] leading-relaxed whitespace-pre-wrap break-words">{job.prompt}</div>
        </div>
      </div>
    </div>
  )
}

// 创建 / 编辑表单对话框
function JobFormDialog({
  api,
  job,
  onClose,
  onSaved,
}: {
  api: () => CronApi | undefined
  job: CronJob | null
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useI18n()
  const [name, setName] = useState(job?.name ?? '')
  const [schedule, setSchedule] = useState(job?.schedule.value ?? '')
  const [prompt, setPrompt] = useState(job?.prompt ?? '')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    if (!name.trim() || !schedule.trim() || !prompt.trim()) return
    setSaving(true)
    setError('')
    try {
      if (job) {
        await api()?.updateCronJob(job.id, {
          name: name.trim(),
          schedule: schedule.trim(),
          prompt: prompt.trim(),
        })
      } else {
        await api()?.createCronJob(name.trim(), schedule.trim(), prompt.trim())
      }
      onSaved()
    } catch (e) {
      setError(errorMessage(e)) // 后端校验错误直接回显
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{job ? t('cron.editTitle') : t('cron.createTitle')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <Field label={t('cron.name')}>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('cron.namePlaceholder')}
              className="w-full px-3 py-2 rounded-lg text-sm bg-canvas/60 border border-line/40 focus:border-primary/40 outline-none transition"
            />
          </Field>
          <Field label={t('cron.schedule')} hint={t('cron.scheduleHint')} error={error}>
            <input
              value={schedule}
              onChange={(e) => setSchedule(e.target.value)}
              placeholder={t('cron.schedulePlaceholder')}
              className={`w-full px-3 py-2 rounded-lg text-sm bg-canvas/60 border outline-none transition font-mono ${
                error ? 'border-error/60' : 'border-line/40 focus:border-primary/40'
              }`}
            />
          </Field>
          <Field label={t('cron.prompt')}>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={t('cron.promptPlaceholder')}
              rows={5}
              className="w-full px-3 py-2 rounded-lg text-sm bg-canvas/60 border border-line/40 focus:border-primary/40 outline-none transition resize-none"
            />
          </Field>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={submit}
              disabled={saving || !name.trim() || !schedule.trim() || !prompt.trim()}
            >
              {job ? t('common.save') : t('cron.create')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string
  hint?: string
  error?: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <div className="text-xs text-muted-foreground mb-1.5">{label}</div>
      {children}
      {error ? (
        <div className="text-xs text-error mt-1.5 break-words">{error}</div>
      ) : (
        hint && <div className="text-[11px] text-muted-foreground/70 mt-1.5">{hint}</div>
      )}
    </label>
  )
}

// RunsSection / RunList 共用的数据拉取：version 变化（有任务完成执行）时重新拉取；
// enabled=false（右栏收起）时不拉——完全不可见还随 cron 事件白拉 50 条，重新可见时依赖变化自动补拉
function useCronRuns(
  api: () => CronApi | undefined,
  jobId: string,
  version: number,
  limit = 20,
  enabled = true,
): CronRun[] | null {
  const [runs, setRuns] = useState<CronRun[] | null>(null)
  useEffect(() => {
    if (!enabled) return
    api()
      ?.listCronRuns(jobId, limit)
      .then((r) => setRuns(r.runs ?? []))
      .catch(() => setRuns([]))
  }, [api, jobId, version, limit, enabled])
  return runs
}

// 单次执行的状态标记：失败/超时 ⚠；成功仅在未读时显示蓝点
function RunStatusMark({ run, unread }: { run: CronRun; unread?: boolean }) {
  if (run.status !== 'success') {
    return (
      <AlertTriangle
        size={12}
        className={`shrink-0 ${run.status === 'timeout' ? 'text-primary' : 'text-error'}`}
      />
    )
  }
  return unread ? <span className="size-1.5 rounded-full bg-info shrink-0" /> : null
}

// 执行记录卡片左侧的时刻（主）+ 日期（次）列，完成条目与运行中活条目共用。
function RunTimeCol({ startedAt, lang }: { startedAt: string; lang: string }) {
  const { date, time } = runParts(startedAt, lang)
  return (
    <div className="flex-1 min-w-0">
      <div className="text-sm font-semibold tabular-nums leading-tight">{time}</div>
      <div className="text-[11px] text-muted-foreground truncate mt-0.5">{date}</div>
    </div>
  )
}

// 执行记录卡片的行内容（时刻为主 + 日期为次 + 耗时 + 状态标记），RunsSection / RunList 共用。
function RunRowInner({ run, lang, unread }: { run: CronRun; lang: string; unread?: boolean }) {
  return (
    <>
      <RunTimeCol startedAt={run.started_at} lang={lang} />
      <span className="text-[11px] text-muted-foreground tabular-nums shrink-0">
        {(run.duration_ms / 1000).toFixed(0)}s
      </span>
      <RunStatusMark run={run} unread={unread} />
    </>
  )
}

// 任务会话右栏的「执行记录」模块（挂在 RightRail 里，定时任务会话置顶）：
// 活条目置顶转圈、点进观测直播；当前查看高亮描边；蓝点=未读；无会话旧记录灰显。
// run 卡外壳：活条目/完成条目/详情页 RunList 行共用基串；高亮与 hover 配方只此一份
const RUN_CARD = 'w-full flex items-center gap-2.5 px-3 py-2.5 text-left transition'
const runCardCls = (active: boolean) =>
  cn(
    CARD_L2,
    RUN_CARD,
    active ? 'border-primary/45 bg-primary/[0.08]' : 'hover:bg-surface/70 hover:border-primary/30',
  )

// memo：观测直播时 App 每 token 重渲染，50 条记录卡不该陪跑（props 经 App 侧
// useMemo/useCallback 稳定：api/onPick/liveRuns）
export const RunsSection = memo(function RunsSection({
  api,
  jobId,
  open,
  activeThread,
  readRuns,
  version,
  liveRuns,
  onPick,
}: {
  api: () => CronApi | undefined
  jobId: string
  open: boolean // 右栏开合：收起时暂停拉取
  activeThread: string | null
  readRuns: Record<string, true>
  version: number
  liveRuns: { thread_id: string; started_at: string }[]
  onPick: (threadId: string) => void
}) {
  const { t, lang } = useI18n()
  const runs = useCronRuns(api, jobId, version, 50, open)
  // 运行中的 run 已完成日志尚未落，故从 liveRuns 单独在顶部渲染活条目；一旦该次跑完
  // 进入 runs（同 thread_id），就从活条目里去掉，避免与完成条目重复。
  const doneThreads = new Set((runs ?? []).map((r) => r.thread_id))
  const live = liveRuns.filter((r) => !doneThreads.has(r.thread_id))

  return (
    <RailSection
      title={t('cron.tabRuns')}
      count={runs === null ? undefined : runs.length + live.length}
    >
      <div className="flex flex-col gap-2.5">
        {/* 运行中的活条目：置顶、转圈、可点进观测直播；跑完后转为下方的完成条目 */}
        {live.map((r) => {
          const active = r.thread_id === activeThread
          return (
            <button key={r.thread_id} onClick={() => onPick(r.thread_id)} className={runCardCls(active)}>
              <RunTimeCol startedAt={r.started_at} lang={lang} />
              <Loader2 size={13} className="shrink-0 animate-spin text-primary" />
            </button>
          )
        })}
        {runs?.length === 0 && live.length === 0 && (
          <div className="px-0.5 py-1 text-xs text-muted-foreground">{t('cron.noRuns')}</div>
        )}
        {(runs ?? []).map((r) => {
          const active = !!r.thread_id && r.thread_id === activeThread
          return (
            <button
              key={r.thread_id || r.started_at}
              disabled={!r.thread_id}
              onClick={() => r.thread_id && onPick(r.thread_id)}
              title={r.error || r.output_summary}
              className={
                !r.thread_id
                  ? cn(CARD_L2, RUN_CARD, 'bg-surface/30 opacity-60 cursor-default')
                  : runCardCls(active)
              }
            >
              <RunRowInner run={r} lang={lang} unread={!!r.thread_id && !readRuns[r.thread_id]} />
            </button>
          )
        })}
      </div>
    </RailSection>
  )
})

// 执行记录列表（详情页左栏）：时间 + 耗时 + 状态标记。
// 有会话的记录点击跳转到该次执行的会话（可续聊）；无会话的旧记录点击展开摘要。
function RunList({
  api,
  jobId,
  version,
  onOpenRun,
}: {
  api: () => CronApi | undefined
  jobId: string
  version: number
  onOpenRun: (threadId: string) => void
}) {
  const { t, lang } = useI18n()
  const runs = useCronRuns(api, jobId, version)
  const [open, setOpen] = useState<number | null>(null)

  if (runs === null) return null
  if (runs.length === 0) {
    return <div className="py-6 text-sm text-muted-foreground">{t('cron.noRuns')}</div>
  }
  return (
    <div className="flex flex-col gap-2">
      {runs.map((r, i) => (
        <div key={r.thread_id || r.started_at} className={`${CARD_L2} overflow-hidden`}>
          <button
            onClick={() =>
              r.thread_id ? onOpenRun(r.thread_id) : setOpen(open === i ? null : i)
            }
            className={cn('group', RUN_CARD, 'hover:bg-surface/70')}
          >
            <RunRowInner run={r} lang={lang} />
            {r.thread_id && (
              <ChevronRight
                size={14}
                className="shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity"
              />
            )}
          </button>
          {open === i && !r.thread_id && (
            <div className={`selectable px-3 pb-3 -mt-0.5 text-xs leading-relaxed whitespace-pre-wrap break-words ${r.error ? 'text-error/90' : 'text-muted-foreground'}`}>
              {r.error || r.output_summary || '—'}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

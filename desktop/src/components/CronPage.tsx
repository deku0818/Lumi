// 定时任务管理整页视图（参考 Cowork Scheduled tasks）。
// 列表页：标题 + 新建按钮 → 信息条幅 → 任务卡片网格；
// 详情页：返回链接 → 大标题 + 编辑/删除/立即运行 → 开关 + 状态 + 下次运行
//        → 左右两栏「执行记录 | 任务内容」。
// 数据经 gateway 的 cron RPC 读写；cron.result/cron.running 事件由 App 转为
// version/runningNames props 驱动刷新，本组件不直接订阅 WS。
import { useEffect, useState } from 'react'
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
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'

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

// 同年省略年份的短格式：6月8日 22:05 / Jun 8, 22:05（执行记录列表用）
const fmtRunTime = (iso: string, lang: string) => {
  const d = new Date(iso)
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const sameYear = d.getFullYear() === new Date().getFullYear()
  const date = d.toLocaleDateString(lang === 'zh' ? 'zh-CN' : 'en-US', {
    ...(sameYear ? {} : { year: 'numeric' }),
    month: 'short',
    day: 'numeric',
  })
  return `${date} ${time}`
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

export function CronPage({
  api,
  jobs,
  runningNames,
  version,
  onOpenRun,
  onRefresh,
}: {
  api: () => CronApi | undefined
  jobs: CronJob[] // 单一数据源：App 持有并下发（侧栏分组共用同一份）
  runningNames: string[]
  version: number
  onOpenRun: (threadId: string, jobId: string) => void
  onRefresh: () => void
}) {
  const { t } = useI18n()
  const [selectedId, setSelectedId] = useState<string | null>(null) // 详情页任务
  const [dialog, setDialog] = useState<{ job: CronJob | null } | null>(null) // 创建/编辑表单
  const [pendingDelete, setPendingDelete] = useState<CronJob | null>(null)

  const toggle = (job: CronJob, enabled: boolean) => {
    api()?.toggleCronJob(job.id, enabled).then(onRefresh).catch(onRefresh)
  }

  const runNow = (job: CronJob) => {
    api()?.runCronJob(job.id).catch(() => {})
  }

  const doDelete = (job: CronJob) => {
    setPendingDelete(null)
    if (selectedId === job.id) setSelectedId(null)
    api()?.deleteCronJob(job.id).then(onRefresh).catch(() => {})
  }

  const selected = selectedId ? jobs.find((j) => j.id === selectedId) : undefined

  return (
    <div className="flex-1 overflow-auto">
      {selected ? (
        <JobDetail
          api={api}
          job={selected}
          running={runningNames.includes(selected.name)}
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
              <p className="text-sm text-muted mt-1">{t('cron.subtitle')}</p>
            </div>
            <Button onClick={() => setDialog({ job: null })} className="shrink-0 rounded-xl gap-1.5">
              <Plus size={15} />
              {t('cron.new')}
            </Button>
          </div>

          <div className="mt-5 flex items-center gap-2.5 rounded-2xl border border-line/30 bg-surface/60 px-4 py-3 text-sm text-muted">
            <Info size={15} className="shrink-0 text-info" />
            {t('cron.banner')}
          </div>

          {jobs.length === 0 ? (
            <div className="mt-16 flex flex-col items-center text-center select-none">
              <Clock size={36} className="text-muted/50" />
              <div className="mt-4 text-ink">{t('cron.empty')}</div>
              <div className="mt-1 text-sm text-muted">{t('cron.emptyHint')}</div>
            </div>
          ) : (
            <div className="mt-5 grid gap-4 grid-cols-[repeat(auto-fill,minmax(230px,1fr))]">
              {jobs.map((job) => (
                <JobCard
                  key={job.id}
                  job={job}
                  running={runningNames.includes(job.name)}
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
          api={api}
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

      <div className="mt-1.5 text-sm text-muted line-clamp-3 flex-1">{job.prompt}</div>

      <div className="mt-3.5 flex items-center justify-between gap-2">
        <span
          className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs ${
            job.enabled ? 'bg-success/10 text-success' : 'bg-line/30 text-muted'
          }`}
        >
          <Clock size={11} />
          {job.enabled ? describeSchedule(job, t) : t('cron.paused')}
        </span>
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
        className="flex items-center gap-1 text-sm text-muted hover:text-ink transition -ml-1"
      >
        <ChevronLeft size={16} />
        {t('cron.back')}
      </button>

      <div className="mt-5 flex items-start justify-between gap-4">
        <h1 className="serif text-3xl min-w-0 break-words">{job.name}</h1>
        <div className="shrink-0 flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" onClick={onEdit} aria-label={t('cron.editTitle')} className="text-muted">
            <Pencil />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={onDelete} aria-label={t('cron.deleteTitle')} className="text-muted">
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
        <span
          className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs ${
            job.enabled ? 'bg-success/10 text-success' : 'bg-line/30 text-muted'
          }`}
        >
          <Clock size={11} />
          {job.enabled ? describeSchedule(job, t) : t('cron.paused')}
        </span>
        {job.enabled && job.next_run && (
          <span className="text-sm text-muted">{t('cron.nextRun', { time: fmtTime(job.next_run) })}</span>
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
          <div className="text-sm text-muted mb-2">{t('cron.tabRuns')}</div>
          <RunList
            api={api}
            jobId={job.id}
            version={version}
            onOpenRun={(tid) => onOpenRun(tid, job.id)}
          />
        </div>
        <div>
          <div className="text-sm text-muted mb-2">{t('cron.prompt')}</div>
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
      // 后端校验错误直接回显；gateway 的 reject 值是 {message} 普通对象，Error 同样有 .message
      setError(String((e as { message?: unknown })?.message ?? e))
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
      <div className="text-xs text-muted mb-1.5">{label}</div>
      {children}
      {error ? (
        <div className="text-xs text-error mt-1.5 break-words">{error}</div>
      ) : (
        hint && <div className="text-[11px] text-muted/70 mt-1.5">{hint}</div>
      )}
    </label>
  )
}

// RunsRail / RunList 共用的数据拉取：version 变化（有任务完成执行）时重新拉取
function useCronRuns(
  api: () => CronApi | undefined,
  jobId: string,
  version: number,
  limit = 20,
): CronRun[] | null {
  const [runs, setRuns] = useState<CronRun[] | null>(null)
  useEffect(() => {
    api()
      ?.listCronRuns(jobId, limit)
      .then((r) => setRuns(r.runs ?? []))
      .catch(() => setRuns([]))
  }, [api, jobId, version, limit])
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

// 任务会话视图的右侧 Runs 栏（参考 Cowork）：列出历次执行，点击切换主区会话。
// 蓝点 = 未读（点开即消失）；无会话的旧记录灰显不可点。
export function RunsRail({
  api,
  jobId,
  activeThread,
  readRuns,
  version,
  onPick,
}: {
  api: () => CronApi | undefined
  jobId: string
  activeThread: string | null
  readRuns: Record<string, true>
  version: number
  onPick: (threadId: string) => void
}) {
  const { t, lang } = useI18n()
  const runs = useCronRuns(api, jobId, version, 50)

  return (
    <aside className="w-60 shrink-0 border-l border-line/20 overflow-auto px-3 py-4">
      <div className="px-2 mb-2 text-xs text-muted">{t('cron.tabRuns')}</div>
      {runs?.length === 0 && (
        <div className="px-2 py-4 text-xs text-muted">{t('cron.noRuns')}</div>
      )}
      {(runs ?? []).map((r) => (
        <button
          key={r.thread_id || r.started_at}
          disabled={!r.thread_id}
          onClick={() => r.thread_id && onPick(r.thread_id)}
          title={r.error || r.output_summary}
          className={`w-full flex items-center gap-2 px-2 py-2 rounded-lg text-left text-sm transition ${
            r.thread_id
              ? r.thread_id === activeThread
                ? 'bg-surface text-ink'
                : 'text-ink/80 hover:bg-surface/60'
              : 'text-muted/50 cursor-default'
          }`}
        >
          <span className="flex-1 truncate">{fmtRunTime(r.started_at, lang)}</span>
          <RunStatusMark run={r} unread={!!r.thread_id && !readRuns[r.thread_id]} />
        </button>
      ))}
    </aside>
  )
}

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
    return <div className="py-6 text-sm text-muted">{t('cron.noRuns')}</div>
  }
  return (
    <div>
      {runs.map((r, i) => (
        <div key={r.thread_id || r.started_at} className="border-b border-line/30 last:border-0">
          <button
            onClick={() =>
              r.thread_id ? onOpenRun(r.thread_id) : setOpen(open === i ? null : i)
            }
            className="group w-full py-3 flex items-center gap-2 text-left hover:bg-surface/40 transition rounded-lg px-1 -mx-1"
          >
            <span className="text-sm flex-1">{fmtRunTime(r.started_at, lang)}</span>
            <span className="text-xs text-muted">{(r.duration_ms / 1000).toFixed(0)}s</span>
            <RunStatusMark run={r} />
            {r.thread_id && (
              <ChevronRight
                size={14}
                className="shrink-0 text-muted opacity-0 group-hover:opacity-100 transition-opacity"
              />
            )}
          </button>
          {open === i && !r.thread_id && (
            <div className={`selectable pb-3 px-1 text-xs leading-relaxed whitespace-pre-wrap break-words ${r.error ? 'text-error/90' : 'text-muted'}`}>
              {r.error || r.output_summary || '—'}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

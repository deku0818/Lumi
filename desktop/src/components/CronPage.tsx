// 定时任务管理整页视图（参考 Cowork Scheduled tasks）。
// 列表页：标题 + 新建按钮 → 信息条幅 → 任务卡片网格；
// 详情页：返回链接 → 大标题 + 编辑/删除/立即运行 → 开关 + 状态 + 下次运行
//        → 左右两栏「执行记录 | 任务内容」。
// 数据经 gateway 的 cron RPC 读写；cron.result/cron.running 事件由 App 转为
// version/activeRuns props 驱动刷新，本组件不直接订阅 WS。
import { useCallback, useState } from 'react'
import {
  ChevronLeft,
  Clock,
  Info,
  Loader2,
  SquarePen,
  Play,
  Plus,
  Trash2,
} from 'lucide-react'
import type { CronActiveRun, CronJob } from '../types'
import { useI18n, type Translate } from '../i18n'
import { ConfirmDialog } from './ConfirmDialog'
import { MachineScope, useMachine } from './MachineTabs'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import { beOf, errorMessage } from '@/lib/utils'
import { CARD_L1, CARD_L1_HOVER } from './glass'
import { Empty } from './SettingsKit'
import { RunRecords } from './CronRuns'
import { useCronRuns, type CronRunsApi } from '../hooks/useCronRuns'

// 后端能力句柄：App 注入所选机器控制连接的 Gateway 子集，便于解耦与测试
export interface CronApi extends CronRunsApi {
  listCronJobs(): Promise<{ jobs: CronJob[] }>
  createCronJob(name: string, schedule: string, prompt: string, projectDir?: string): Promise<{ job: CronJob }>
  updateCronJob(
    jobId: string,
    fields: { name?: string; schedule?: string; prompt?: string },
  ): Promise<{ job: CronJob }>
  deleteCronJob(jobId: string): Promise<{ job_id: string }>
  toggleCronJob(jobId: string, enabled: boolean): Promise<{ job: CronJob }>
  runCronJob(jobId: string): Promise<{ ok: boolean }>
}

const pad = (n: number) => String(n).padStart(2, '0')

const fmtTime = (iso: string) => {
  const d = new Date(iso)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// 调度规则人类可读化：覆盖常见形态（间隔简写、每天/每周 cron、一次性），
// 其余 cron 表达式按原文显示（足够看懂，不重造 cron 解析器）。
// 项目主页的定时卡片共用：两处调度文案一致
export function describeSchedule(job: CronJob, t: Translate): string {
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
  initialMachine = 'local',
  initialJobId,
  createIn,
  jobs,
  activeRuns,
  version,
  onOpenRun,
  onRefresh,
}: {
  api: (backend: string) => CronApi | undefined // 按机器取连接（定时是 per-机器）
  initialMachine?: string // 打开时选中的机器（从项目主页跳来 = 该项目所在机器）
  initialJobId?: string // 打开时直达的任务详情（从项目主页点某条任务）
  createIn?: string // 打开时即弹创建表单并绑定该项目（项目主页「新建任务」）
  jobs: CronJob[] // App 持有的跨机器合并列表（带 backend 标记）
  activeRuns: Record<string, CronActiveRun[]>
  version: number
  onOpenRun: (threadId: string, jobId: string) => void
  onRefresh: (backend: string) => void // 只刷这台机器的任务列表
}) {
  const { t } = useI18n()
  // 方案甲「先选机器」：定时任务在各自机器的调度器上跑，按机器管理。
  const [machine, setMachine] = useState(initialMachine)
  const [selectedId, setSelectedId] = useState<string | null>(initialJobId ?? null) // 详情页任务
  // 创建/编辑表单；projectDir 仅创建时用（项目主页新建带上所在项目）
  const [dialog, setDialog] = useState<{ job: CronJob | null; projectDir?: string } | null>(
    createIn ? { job: null, projectDir: createIn } : null,
  )
  const [pendingDelete, setPendingDelete] = useState<CronJob | null>(null)

  const offline = useMachine(machine).scope !== 'connected'
  const gw = api(machine)
  // 传给子组件：操作落在选中机器。稳定引用——否则每次渲染换新身份会让 useCronRuns 反复重拉。
  const boundApi = useCallback(() => gw, [gw])
  const machineRuns = activeRuns[machine]
  const shownJobs = jobs.filter((j) => (j.backend || 'local') === machine)
  // 稳定引用：MachineScope 的自动落回效应把它当依赖，每渲染换身份会让效应反复重挂
  const pickMachine = useCallback((m: string) => {
    setSelectedId(null)
    setMachine(m)
  }, [])

  const toggle = (job: CronJob, enabled: boolean) => {
    const refresh = () => onRefresh(machine)
    gw?.toggleCronJob(job.id, enabled).then(refresh).catch(refresh)
  }

  const runNow = (job: CronJob) => {
    gw?.runCronJob(job.id).catch(() => {})
  }

  const doDelete = (job: CronJob) => {
    setPendingDelete(null)
    if (selectedId === job.id) setSelectedId(null)
    gw?.deleteCronJob(job.id).then(() => onRefresh(machine)).catch(() => {})
  }

  const selected = selectedId ? shownJobs.find((j) => j.id === selectedId) : undefined

  return (
    <div className="flex-1 overflow-auto">
      {selected ? (
        <JobDetail
          api={boundApi}
          job={selected}
          machineRuns={machineRuns}
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
            {/* 同 ProjectsPage：新建在选择条上方，选中机器离线时自行禁用 */}
            <Button
              onClick={() => setDialog({ job: null })}
              disabled={offline}
              className="shrink-0 rounded-xl gap-1.5"
            >
              <Plus size={15} />
              {t('cron.new')}
            </Button>
          </div>

          <MachineScope
            value={machine}
            onChange={pickMachine}
            tabsClassName="mt-4 flex flex-wrap gap-2"
          >

          <div className={`mt-5 flex items-center gap-2.5 ${CARD_L1} px-4 py-3 text-sm text-muted-foreground`}>
            <Info size={15} className="shrink-0 text-info" />
            {t('cron.banner')}
          </div>

          {shownJobs.length === 0 ? (
            <Empty size="page" icon={<Clock />} title={t('cron.empty')}>
              {t('cron.emptyHint')}
            </Empty>
          ) : (
            <div className="mt-5 grid gap-4 grid-cols-[repeat(auto-fill,minmax(230px,1fr))]">
              {shownJobs.map((job) => (
                <JobCard
                  key={job.id}
                  job={job}
                  running={machineRuns?.some(r => r.job_id === job.id) ?? false}
                  onOpen={() => setSelectedId(job.id)}
                  onToggle={(v) => toggle(job, v)}
                />
              ))}
            </div>
          )}
          </MachineScope>
        </div>
      )}

      {dialog && (
        <JobFormDialog
          api={boundApi}
          job={dialog.job}
          projectDir={dialog.projectDir}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null)
            onRefresh(machine)
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
  return (
    <div
      className={`flex flex-col ${CARD_L1} ${CARD_L1_HOVER} cursor-pointer p-4 ${job.enabled ? '' : 'opacity-55'}`}
      onClick={onOpen}
    >
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0 font-medium truncate">{job.name}</div>
        {running && <Loader2 size={15} className="shrink-0 mt-0.5 animate-spin text-primary" />}
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
  machineRuns,
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
  machineRuns?: CronActiveRun[]
  version: number
  onBack: () => void
  onEdit: () => void
  onDelete: () => void
  onToggle: (enabled: boolean) => void
  onRunNow: () => void
  onOpenRun: (threadId: string, jobId: string) => void
}) {
  const { t } = useI18n()
  const entries = useCronRuns(api, beOf(job), job.id, version, machineRuns)
  const running = machineRuns?.some(r => r.job_id === job.id) ?? false
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
            <SquarePen />
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

      <div className="mt-8 pt-6 border-t border-line/30 grid grid-cols-1 md:grid-cols-[5fr_7fr] gap-10">
        <div>
          <div className="text-sm text-muted-foreground mb-2">{t('cron.tabRuns')}</div>
          <RunRecords entries={entries} onPick={(tid) => onOpenRun(tid, job.id)} />
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
  projectDir,
  onClose,
  onSaved,
}: {
  api: () => CronApi | undefined
  job: CronJob | null
  projectDir?: string
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
        await api()?.createCronJob(name.trim(), schedule.trim(), prompt.trim(), projectDir)
      }
      onSaved()
    } catch (e) {
      setError(errorMessage(e)) // 后端校验错误直接回显
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg p-6">
        <DialogHeader>
          <DialogTitle>{job ? t('cron.editTitle') : t('cron.createTitle')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          {projectDir && (
            <div className="text-xs text-muted-foreground">
              {t('cron.project')}
              <span className="ml-1.5 font-mono text-ink/80 break-all">{projectDir}</span>
            </div>
          )}
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

import { useCallback, useEffect, useState } from 'react'
import {
  Check,
  ChevronDown,
  ChevronRight,
  Plus,
  Send,
  Building2,
  X,
  Folder,
  FolderPlus,
  AlertTriangle,
} from 'lucide-react'
import type {
  ChannelInfo,
  FeishuConfig,
  CheckTone,
  DiagnoseCheck,
  Project,
  ProviderProfile,
} from '../types'
import type { Gateway } from '../gateway'
import { MachineTabs } from './MachineTabs'
import { DirBrowser } from './DirBrowser'
import { basename } from '@/lib/utils'
import { Section, Card, Field, TextInput, SegmentedControl, FormModal } from './SettingsKit'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

const STATUS_LABEL: Record<string, string> = {
  off: '未启用',
  stopped: '已停止',
  connecting: '连接中',
  connected: '已连接',
  error: '连接失败',
}

// 状态光点（demo 方案 A）：绿=已连接、金=连接中（呼吸）、红=失败，未启用/停止为无光晕灰点
const STATUS_DOT: Record<string, string> = {
  connected: 'bg-success shadow-[0_0_6px_var(--color-success)]',
  connecting: 'bg-primary shadow-[0_0_6px_var(--color-accent)] animate-pulse',
  error: 'bg-error shadow-[0_0_6px_var(--color-error)]',
  off: 'bg-separator',
  stopped: 'bg-separator',
}

const emptyFeishu = (): FeishuConfig => ({
  enabled: false,
  app_id: '',
  app_secret: '',
  allow_from: ['*'],
  group_policy: 'mention',
  model: '',
  effort: 'auto',
  tool_mode: 'auto',
  workspace: '',
  minutes_enabled: false,
  daily_dream_enabled: false,
  daily_dream_time: '03:00',
  summary_max_concurrency: 3,
})

// 渠道面板（设置 → 渠道）。列表视图：各 IM 渠道卡片（状态灯 + 开关 + 编辑）；
// 表单视图：飞书配置（凭证 / 审批模式 / 群策略 / 白名单）。配置存后端 ~/.lumi/lumi.json，
// 保存即实时停旧起新。
export function ChannelsPanel({
  machines,
  gwFor,
}: {
  machines: { id: string; name: string }[]
  gwFor: (id: string) => Gateway | undefined
}) {
  const [machine, setMachine] = useState('local')
  const [list, setList] = useState<ChannelInfo[]>([])
  const [providers, setProviders] = useState<ProviderProfile[]>([])
  const [editing, setEditing] = useState<FeishuConfig | null>(null) // null = 列表视图

  const gw = gwFor(machine)
  const reload = useCallback(() => {
    gwFor(machine)
      ?.getChannels()
      .then((r) => setList(r.channels ?? []))
      .catch(() => setList([]))
  }, [gwFor, machine])

  // 渠道连接是异步的（enable 后先 connecting 再 connected/error），挂载期间轮询
  // 保持状态新鲜；get_channels 只读内存状态，开销可忽略
  useEffect(() => {
    reload()
    const timer = setInterval(reload, 3000)
    return () => clearInterval(timer)
  }, [reload])

  // 该机器的供应商 profiles（渠道「模型 + 思考」配置的模型清单与思考能力来源）
  useEffect(() => {
    gwFor(machine)
      ?.listProviders()
      .then((r) => setProviders(r.profiles ?? []))
      .catch(() => setProviders([]))
  }, [gwFor, machine])

  const feishu = list.find((c) => c.name === 'feishu')

  const save = (config: FeishuConfig) =>
    gw
      ?.saveChannel('feishu', config)
      .then((r) => {
        setList(r.channels ?? [])
        setEditing(null)
      })
      .catch(() => {})

  // 列表开关：仅翻转 enabled 立即保存（凭证编辑走表单）
  const toggleEnabled = (on: boolean) => {
    const cfg = feishu?.config ?? emptyFeishu()
    save({ ...cfg, enabled: on })
  }

  return (
    <div>
      <MachineTabs machines={machines} value={machine} onChange={setMachine} />
      <Section
        title="渠道"
        desc={
          <>
            把 Lumi 接入飞书等 IM。凭证存该机器的 <code>~/.lumi/lumi.json</code>（限本人可读），
            保存后实时重连。全程 AI 审批，仅保留 ask 询问卡片。
          </>
        }
      >
        <div className="space-y-2">
          {/* 飞书 */}
          <ChannelCard
            icon={<Send size={17} />}
            title="飞书"
            status={feishu?.status}
            enabled={!!feishu?.enabled}
            subtitle={feishuSubtitle(feishu)}
            onToggle={toggleEnabled}
            onEdit={() => setEditing(feishu?.config ?? emptyFeishu())}
          />

          {/* 企业微信（即将支持） */}
          <Card className="flex items-center gap-3 opacity-55">
            <div className="grid place-items-center w-9 h-9 rounded-lg bg-surface border border-line text-muted-foreground">
              <Building2 size={17} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-medium">企业微信</div>
              <div className="text-[11px] text-muted-foreground mt-0.5">即将支持</div>
            </div>
            <span className="text-[10.5px] px-2 py-0.5 rounded-full border border-separator text-muted-foreground">
              即将支持
            </span>
          </Card>
        </div>
      </Section>

      {editing && (
        <FeishuForm
          initial={editing}
          gw={gw}
          providers={providers}
          onCancel={() => setEditing(null)}
          onSave={save}
        />
      )}
    </div>
  )
}

function feishuSubtitle(c?: ChannelInfo): string {
  if (!c?.enabled) return '未启用'
  const mode = c.config.tool_mode === 'auto' ? 'AI 审批' : '特权放行'
  const who = c.config.allow_from.includes('*') ? '所有人可用' : `${c.config.allow_from.length} 人白名单`
  return `${mode} · ${who}`
}

function ChannelCard({
  icon,
  title,
  status,
  enabled,
  subtitle,
  onToggle,
  onEdit,
}: {
  icon: React.ReactNode
  title: string
  status?: { state: string; detail: string }
  enabled: boolean
  subtitle: string
  onToggle: (on: boolean) => void
  onEdit: () => void
}) {
  const state = status?.state ?? 'off'
  // error 态用后端给的具体原因（缺凭证 / 未装 lark…）替代泛化副标题
  const sub = state === 'error' && status?.detail ? status.detail : subtitle
  return (
    <Card className="flex items-center gap-3">
      <div className="grid place-items-center w-9 h-9 rounded-lg bg-surface border border-line text-ink">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-medium flex items-center gap-2">
          {title}
          <span className={`size-2 rounded-full shrink-0 ${STATUS_DOT[state] ?? STATUS_DOT.off}`} />
          <span
            className={`text-[11px] font-normal ${state === 'error' ? 'text-error' : 'text-muted-foreground'}`}
          >
            {STATUS_LABEL[state]}
          </span>
        </div>
        <div
          className={`text-[11px] mt-0.5 truncate ${state === 'error' ? 'text-[var(--color-error)]' : 'text-muted-foreground'}`}
        >
          {sub}
        </div>
      </div>
      <Switch checked={enabled} onCheckedChange={onToggle} />
      <Button variant="ghost" size="sm" onClick={onEdit} className="text-muted-foreground">
        编辑
      </Button>
    </Card>
  )
}

// 一次体检的完整状态机。两条链路（接入 / 妙记）逐字同构，合一份免得能力只接一半
// ——error 态就曾只接了接入侧，妙记仍把失败折叠成 null（与「从未检查过」无从区分）。
function useDiagnose(call: () => Promise<{ checks: DiagnoseCheck[] }> | undefined) {
  const [checks, setChecks] = useState<DiagnoseCheck[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const run = () => {
    const p = call()
    if (!p) return // 未连接
    setLoading(true)
    setError('')
    p.then((r) => setChecks(r.checks))
      .catch((e) => setError(String(e?.message || e) || '体检请求失败'))
      .finally(() => setLoading(false))
  }
  return { checks, loading, error, run }
}

function FeishuForm({
  initial,
  gw,
  providers,
  onCancel,
  onSave,
}: {
  initial: FeishuConfig
  gw?: Gateway
  providers: ProviderProfile[]
  onCancel: () => void
  onSave: (cfg: FeishuConfig) => void
}) {
  const [cfg, setCfg] = useState<FeishuConfig>(initial)
  const set = (patch: Partial<FeishuConfig>) => setCfg((c) => ({ ...c, ...patch }))

  // 接入体检：权限 / 事件订阅 / 版本发布任缺其一，机器人都是「连上了但不回消息」，
  // 且开放平台不报任何错。四项由一次「应用版本信息」查询判定。
  const setup = useDiagnose(() =>
    gw?.diagnoseFeishuSetup('feishu', { app_id: cfg.app_id, app_secret: cfg.app_secret }),
  )
  // 妙记体检：走 lark-cli 子进程 + 网络，耗时 1-2s
  const minutes = useDiagnose(() => gw?.diagnoseMinutes('feishu', { app_id: cfg.app_id }))

  useEffect(() => {
    // 没凭证就别查——四项必然全红，属于噪音而非信息
    if (initial.app_id && initial.app_secret) setup.run()
    if (initial.minutes_enabled) minutes.run()
    // 只在弹窗打开时查一次；凭证改动后靠「重新检查」手动触发，输入途中不打扰
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const allowAll = cfg.allow_from.includes('*')

  // 没有独立的「测试连接」：接入体检第①项已验凭证，且信息更全。两者并存会矛盾
  // ——凭证对但缺 app_version 权限时，bot/v3/info 报成功而体检报失败
  const footer = (
    <>
      <div className="flex-1" />
      <Button variant="ghost" onClick={onCancel}>
        取消
      </Button>
      <Button onClick={() => onSave(cfg)}>保存并重连</Button>
    </>
  )

  return (
    <FormModal
      onClose={onCancel}
      title="飞书配置"
      footer={footer}
      className="sm:max-w-2xl"
      bodyClassName="max-h-[66vh]"
    >
      <div className="space-y-4">
        <Field label="App ID" hint="支持 ${FEISHU_APP_ID} 引用环境变量">
          <TextInput value={cfg.app_id} onChange={(e) => set({ app_id: e.target.value })} placeholder="cli_…" />
        </Field>
        <Field label="App Secret" hint="chmod 600 存 ~/.lumi/lumi.json，不写入项目目录">
          <TextInput password value={cfg.app_secret} onChange={(e) => set({ app_secret: e.target.value })} placeholder="●●●●" />
        </Field>

        <Field
          label="接入体检"
          hint="权限 / 事件订阅 / 版本发布，缺任一机器人都收不到消息且开放平台不报错"
        >
          <CheckPanel
            key={panelKey(setup.checks)}
            {...setup}
            subject="机器人接入"
            ready="已就绪 · 可正常收发消息"
          />
        </Field>

        <Field label="群消息策略">
          <SegmentedControl
            value={cfg.group_policy}
            onChange={(v) => set({ group_policy: v as FeishuConfig['group_policy'] })}
            options={[
              { val: 'mention', label: '@我才回' },
              { val: 'open', label: '响应全部' },
            ]}
          />
        </Field>

        <Field label="可用成员（白名单）" hint={allowAll ? '所有人可用' : '仅列表内 open_id 可用；为空 = 全部拒绝'}>
          <SegmentedControl
            value={allowAll ? 'all' : 'list'}
            onChange={(v) => set({ allow_from: v === 'all' ? ['*'] : [] })}
            options={[
              { val: 'all', label: '所有人' },
              { val: 'list', label: '指定成员' },
            ]}
          />
          {!allowAll && (
            <ChipEditor values={cfg.allow_from} onChange={(vals) => set({ allow_from: vals })} />
          )}
        </Field>

        <ChannelRuntimeFields cfg={cfg} set={set} providers={providers} gw={gw} />

        <MinutesSection
          cfg={cfg}
          set={(patch) => {
            set(patch)
            // 刚打开开关时立刻体检，省得用户还要手点一次「检查」
            if (patch.minutes_enabled && !minutes.checks) minutes.run()
          }}
          diagnose={minutes}
        />

        <DailyDreamSection cfg={cfg} set={set} />
      </div>
    </FormModal>
  )
}

// 每日记忆整理（Dream）：开关 + 时间 + summary 最大并发。关时只留标题行（时间/并发隐藏）。
// 妙记纪要分组。链路有四个彼此独立的前置条件（lark-cli / 授权 / 权限 / 订阅），
// 任一断裂的表现完全相同——静默收不到事件、零报错——故做成逐项诊断，把「不工作」
// 变成「卡在第几步」。正常态只显示一行绿，异常时自动展开定位问题。
function MinutesSection({
  cfg,
  set,
  diagnose,
}: {
  cfg: FeishuConfig
  set: (patch: Partial<FeishuConfig>) => void
  diagnose: ReturnType<typeof useDiagnose>
}) {
  return (
    <ToggleCard
      icon="🎙️"
      title="妙记纪要"
      desc="录音 / 会议结束后自动整理纪要，推送到私聊"
      tone="info"
      checked={cfg.minutes_enabled}
      onCheckedChange={(on) => set({ minutes_enabled: on })}
    >
      <CheckPanel
        key={panelKey(diagnose.checks)}
        {...diagnose}
        subject="妙记链路"
        ready="已就绪 · 妙记生成后自动推送纪要"
      />
    </ToggleCard>
  )
}

// 每轮新结果重新挂载：展开态回到「异常自动展开」，无需 effect 重置
const panelKey = (checks: DiagnoseCheck[] | null) =>
  checks?.map((c) => `${c.key}${c.tone}`).join() ?? 'none'

// 逐项体检面板：机器人接入与妙记链路共用。正常态收成一行，异常时自动展开定位到具体步骤。
// subject 派生出「正在检查 X…」「检查 X」，只有就绪语（ready）各链路不同
function CheckPanel({
  checks,
  loading,
  error,
  run,
  subject,
  ready,
}: ReturnType<typeof useDiagnose> & { subject: string; ready: string }) {
  const bad = checks?.filter((c) => c.tone === 'error') ?? []
  const warned = checks?.filter((c) => c.tone === 'warn') ?? []
  // 有问题就默认展开——用户不该为了知道哪里坏了还多点一次；之后随用户手动开合
  const [open, setOpen] = useState(bad.length > 0)

  if (loading)
    return (
      <div className="flex items-center gap-2.5 rounded-lg border border-line bg-surface/60 px-3 py-2.5 text-xs text-muted-foreground">
        <span className="lumi-orb" style={{ width: 11, height: 11 }} />
        正在检查{subject}…
      </div>
    )

  // 体检没跑成时说清楚为什么，而不是退回「没检查过」的样子让用户空点
  if (error)
    return (
      <div className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-xs ${TONE_BANNER.error}`}>
        <StatusDot tone="error" />
        <span className="flex-1">体检未能执行：{error}</span>
        <button onClick={run} className="text-[11px] text-muted-foreground hover:text-ink">
          重试
        </button>
      </div>
    )

  if (!checks)
    return (
      <button
        onClick={run}
        className="rounded-lg border border-line bg-surface px-3 py-2 text-xs text-muted-foreground hover:text-ink"
      >
        检查{subject}
      </button>
    )

  const tone: CheckTone = bad.length ? 'error' : warned.length ? 'warn' : 'ok'
  return (
    <>
      <div className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-xs ${TONE_BANNER[tone]}`}>
        <StatusDot tone={tone} />
        <span className="flex-1">
          {tone === 'error'
            ? `${bad.length} 项未就绪：${bad[0].name}`
            : tone === 'warn'
              ? `${ready}，${warned.length} 项功能降级`
              : ready}
        </span>
        <button onClick={run} className="text-[11px] text-muted-foreground hover:text-ink">
          重新检查
        </button>
      </div>

      <button
        onClick={() => setOpen((v) => !v)}
        className="mt-2 inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-ink"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {open ? '收起检查详情' : '查看检查详情'}
      </button>

      {open && (
        <div className="mt-2">
          {checks.map((c) => (
            <CheckRow key={c.key} check={c} />
          ))}
        </div>
      )}
    </>
  )
}

// 三色语义的配色表；语义本身由后端定（DiagnoseCheck.tone），前端只管配色
const TONE_BANNER: Record<CheckTone, string> = {
  ok: 'border border-success/25 bg-success/10 text-success',
  warn: 'border border-primary/40 bg-primary/10 text-primary',
  error: 'border border-error/30 bg-error/10 text-error',
}

const TONE_DOT: Record<CheckTone, string> = {
  ok: 'bg-success shadow-[0_0_6px_var(--color-success)]',
  warn: 'bg-primary shadow-[0_0_6px_var(--color-accent)]',
  error: 'bg-error shadow-[0_0_6px_var(--color-error)]',
}

function StatusDot({ tone }: { tone: CheckTone }) {
  return <span className={`size-2 rounded-full shrink-0 ${TONE_DOT[tone]}`} />
}

function CheckRow({ check }: { check: DiagnoseCheck }) {
  return (
    <div className="flex items-start gap-2.5 py-1.5 border-t border-line/55 first:border-t-0">
      <span className="mt-1.5">
        <StatusDot tone={check.tone} />
      </span>
      <div className="flex-1 min-w-0">
        <div className={check.tone === 'error' ? 'text-xs text-error' : 'text-xs'}>{check.name}</div>
        {(check.detail || check.emphasis) && (
          <div className="text-[11px] text-muted-foreground mt-0.5 break-words">
            {check.detail}
            {/* 加粗项是这行里用户唯一需要据以决策的信息（如哪些功能不可用） */}
            {check.emphasis && <strong className="text-ink">{check.emphasis}</strong>}
          </div>
        )}
        {check.fix_cmd && (
          <div className="mt-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 font-mono text-[11px] select-all overflow-x-auto whitespace-nowrap">
            {check.fix_cmd}
          </div>
        )}
        {check.fix_url && (
          <a
            href={check.fix_url}
            target="_blank"
            rel="noreferrer"
            className="mt-1.5 inline-flex items-center gap-1 rounded-lg border border-primary/40 bg-primary/10 px-2.5 py-1 text-[11px] text-primary hover:bg-primary/20"
          >
            去开放平台配置 ↗
          </a>
        )}
        {check.fix_note && (
          <div className="text-[11px] text-muted-foreground mt-1">{check.fix_note}</div>
        )}
      </div>
    </div>
  )
}

// 带开关的分组卡：图标 + 标题 + 副标题 + Switch，开启时展开 children。
// Dream / 妙记两个分组共用，免得同一套卡壳在同一文件里各写一遍后各自漂移。
function ToggleCard({
  icon,
  title,
  desc,
  tone,
  checked,
  onCheckedChange,
  bodyClass = 'px-4 pb-4 pt-1',
  children,
}: {
  icon: string
  title: string
  desc: string
  tone: 'primary' | 'info'
  checked: boolean
  onCheckedChange: (on: boolean) => void
  bodyClass?: string
  children?: React.ReactNode
}) {
  const ring =
    tone === 'primary' ? 'border-primary/30 bg-primary/5' : 'border-info/25 bg-info/5'
  return (
    <div className={`rounded-xl border overflow-hidden ${ring}`}>
      <div className="flex items-center gap-3 px-4 py-3.5">
        <div className="grid place-items-center w-8 h-8 rounded-lg bg-surface border border-line text-base">
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-medium">{title}</div>
          <div className="text-[11px] text-muted-foreground mt-0.5">{desc}</div>
        </div>
        <Switch checked={checked} onCheckedChange={onCheckedChange} />
      </div>
      {checked && children && <div className={bodyClass}>{children}</div>}
    </div>
  )
}

function DailyDreamSection({
  cfg,
  set,
}: {
  cfg: FeishuConfig
  set: (patch: Partial<FeishuConfig>) => void
}) {
  return (
    <ToggleCard
      icon="🌙"
      title="每日记忆整理（Dream）"
      desc="到点自动沉淀记忆 + 压缩会话，长会话不再无限膨胀"
      tone="primary"
      checked={cfg.daily_dream_enabled}
      onCheckedChange={(on) => set({ daily_dream_enabled: on })}
      bodyClass="grid grid-cols-2 gap-4 px-4 pb-4 pt-1"
    >
      <Field label="执行时间（每天）" hint="建议选低峰时段">
        <TextInput
          type="time"
          value={cfg.daily_dream_time}
          onChange={(e) => set({ daily_dream_time: e.target.value })}
        />
      </Field>
      <Field label="Summary 最大并发" hint="限流防接口 429；dream 恒串行">
        <TextInput
          type="number"
          min={1}
          max={8}
          value={cfg.summary_max_concurrency}
          onChange={(e) =>
            set({
              summary_max_concurrency: Math.min(
                8,
                Math.max(1, Number(e.target.value) || 1),
              ),
            })
          }
        />
      </Field>
    </ToggleCard>
  )
}

// 档位显示名（对齐 ModelPicker）：auto→自动 / on→On / 其余首字母大写
const levelLabel = (lv: string) =>
  lv === 'auto' ? '自动' : lv === 'on' ? 'On' : lv.charAt(0).toUpperCase() + lv.slice(1)

// 渠道「会话运行时」通用块：模型 + 思考档位 + 工具审批 + 绑定项目。各 IM 渠道复用同一块
// （对齐后端 ChannelRuntimeConfig），值各渠道各存一份。model 空 = 跟随 desktop 全局。
function ChannelRuntimeFields({
  cfg,
  set,
  providers,
  gw,
}: {
  cfg: FeishuConfig
  set: (patch: Partial<FeishuConfig>) => void
  providers: ProviderProfile[]
  gw?: Gateway
}) {
  // source 用本地状态表达用户意图（而非纯派生 !!cfg.model）：providers 尚未加载完时
  // 也能切到「指定」进入选择视图（下拉负责选模型），不再因 firstModel='' 静默无反应
  const [source, setSource] = useState<'global' | 'custom'>(cfg.model ? 'custom' : 'global')
  const custom = source === 'custom'
  // 某模型的思考能力：遍历 profiles 取第一处含该 model 的 thinking 条目（同名跨 provider 取先者）
  const capOf = (model: string) => {
    for (const p of providers) {
      const t = p.thinking?.[model]
      if (t) return t
    }
    return undefined
  }
  // 切模型时把档位收敛到该模型合法值；非法 → auto（最安全，不注入思考参数）
  const coerce = (model: string, eff: string) => {
    const cap = capOf(model)
    if (!cap || cap.control === 'none') return 'auto'
    return (cap.levels ?? ['auto']).includes(eff) ? eff : 'auto'
  }
  const firstModel = providers.find((p) => p.models.length)?.models[0] ?? ''
  const cap = custom ? capOf(cfg.model) : undefined
  const control = cap?.control ?? 'none'

  return (
    <div className="rounded-xl border border-info/25 bg-info/5 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3.5">
        <div className="grid place-items-center w-8 h-8 rounded-lg bg-surface border border-line text-base">
          ⚙
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-medium">会话运行时</div>
          <div className="text-[11px] text-muted-foreground mt-0.5">
            这个渠道的 Agent 用什么模型、怎么思考、怎么审批、在哪个项目跑
          </div>
        </div>
      </div>
      <div className="space-y-4 px-4 pb-4 pt-1">
        <Field
          label="模型来源"
          hint="跟随全局＝用 desktop 当前模型；指定＝本渠道独立，与 desktop 互不影响"
        >
          <SegmentedControl
            value={custom ? 'custom' : 'global'}
            onChange={(v) => {
              setSource(v as 'global' | 'custom')
              if (v === 'global') set({ model: '', effort: 'auto' })
              // 切「指定」时有可用模型就补第一个；providers 未加载完（firstModel=''）则
              // 先进 custom 视图，由模型下拉待选，加载后即可选
              else if (!cfg.model && firstModel) set({ model: firstModel, effort: 'auto' })
            }}
            options={[
              { val: 'global', label: '跟随 desktop 全局' },
              { val: 'custom', label: '为本渠道指定' },
            ]}
          />
        </Field>

        {custom && (
          <Field label="模型">
            <ModelDropdown
              providers={providers}
              value={cfg.model}
              onPick={(m) => set({ model: m, effort: coerce(m, cfg.effort) })}
            />
            <ThinkingControl
              control={control}
              levels={cap?.levels ?? ['auto']}
              effort={cfg.effort}
              onPick={(e) => set({ effort: e })}
            />
          </Field>
        )}

        <Field label="工具审批模式" hint="两种模式下泄漏的人工审批一律自动拒绝；仅保留 ask 询问卡片">
          <SegmentedControl
            value={cfg.tool_mode}
            onChange={(v) => set({ tool_mode: v as FeishuConfig['tool_mode'] })}
            options={[
              { val: 'auto', label: 'AI 审批' },
              { val: 'privileged', label: '特权放行' },
            ]}
          />
        </Field>

        <WorkspacePicker gw={gw} value={cfg.workspace} onChange={(v) => set({ workspace: v })} />
      </div>
    </div>
  )
}

// 模型下拉：按 provider 分组列出所有模型（对齐 ModelPicker 的 More models 子菜单）。
function ModelDropdown({
  providers,
  value,
  onPick,
}: {
  providers: ProviderProfile[]
  value: string
  onPick: (m: string) => void
}) {
  const prov = providers.find((p) => p.models.includes(value))
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="group flex w-full items-center gap-2.5 rounded-lg border border-line bg-surface px-3 py-2 text-left outline-none transition data-[state=open]:border-primary"
        >
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm text-ink">{value || '选择模型'}</div>
            {prov && <div className="truncate text-[10.5px] text-muted-foreground">{prov.name}</div>}
          </div>
          <ChevronDown
            size={14}
            className="shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
          />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 w-[--radix-dropdown-menu-trigger-width] overflow-auto">
        {providers.length === 0 && (
          <div className="px-2 py-1.5 text-xs text-muted-foreground">该机器暂无供应商</div>
        )}
        {providers.map((p, i) => (
          <div key={p.id}>
            {i > 0 && <DropdownMenuSeparator />}
            <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-muted-foreground/70">
              {p.name}
            </div>
            {p.models.map((m) => (
              <DropdownMenuItem key={m} onClick={() => onPick(m)}>
                <Check className={`text-primary ${m === value ? 'opacity-100' : 'opacity-0'}`} />
                <span className="truncate">{m}</span>
              </DropdownMenuItem>
            ))}
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// 思考档位控制：随模型能力变形——effort 分段（ultra 同排、金色标示顶档）/ toggle 开关 / none 隐藏。
function ThinkingControl({
  control,
  levels,
  effort,
  onPick,
}: {
  control: 'none' | 'effort' | 'toggle'
  levels: string[]
  effort: string
  onPick: (e: string) => void
}) {
  if (control === 'none') {
    return (
      <div className="mt-3 border-t border-dashed border-line pt-3 text-[11.5px] text-muted-foreground">
        ◦ 该模型无思考控制
      </div>
    )
  }
  if (control === 'toggle') {
    // toggle 型：auto（未显式设）按 On 展示，与 ModelPicker 一致
    const on = effort === 'on' || effort === 'auto'
    return (
      <div className="mt-3 flex items-center gap-2 border-t border-dashed border-line pt-3">
        <span className="flex-1 text-xs text-ink">深度思考（Thinking）</span>
        <Switch checked={on} onCheckedChange={(v) => onPick(v ? 'on' : 'off')} />
      </div>
    )
  }
  return (
    <div className="mt-3 border-t border-dashed border-line pt-3">
      <div className="mb-1.5 text-xs text-muted-foreground">思考档位（Effort）</div>
      {/* Ultra 与原生档位同排：金色标示 Lumi 顶档（沿用 ModelPicker chip 惯例），不单独占行 */}
      <SegmentedControl
        value={effort}
        onChange={onPick}
        options={levels.map((l) => ({
          val: l,
          label: l === 'ultra' ? <span className="font-medium text-primary">Ultra</span> : levelLabel(l),
        }))}
      />
      {effort === 'ultra' && (
        <div className="mt-1.5 text-[10.5px] text-muted-foreground">思考拉满 + 解锁 workflow 编排</div>
      )}
    </div>
  )
}

// 绑定项目：从该机器已登记的项目里挑一个作为飞书工作目录（参考 .demos/feishu-project-select.html A）。
// 不再让用户手填路径——先在「项目」页建项目，渠道里只选已有项目；切换已绑定项目会弹重置提醒。
// 空 = serve 进程当前目录（兜底，不推荐）。
function WorkspacePicker({
  gw,
  value,
  onChange,
}: {
  gw?: Gateway
  value: string
  onChange: (v: string) => void
}) {
  const [projects, setProjects] = useState<Project[]>([])
  const [creating, setCreating] = useState(false) // DirBrowser 新建项目中
  const [pending, setPending] = useState<string | null>(null) // 待确认切换的目标路径

  useEffect(() => {
    gw
      ?.listProjects()
      .then((r) => setProjects(r.projects ?? []))
      .catch(() => setProjects([]))
  }, [gw])

  const current = projects.find((p) => p.path === value)
  // 触发器展示：已登记取项目名 / 仅有路径取 basename / 未绑定走兜底
  const shown = current
    ? { name: current.name, path: value }
    : value
      ? { name: basename(value), path: value }
      : { name: 'serve 进程当前目录', path: '兜底，不推荐' }

  // 选中项目：与当前不同且已有绑定 → 弹确认；否则直接生效
  const choose = (path: string) => {
    if (path === value) return
    if (value) setPending(path)
    else onChange(path)
  }

  // 新建项目：浏览目录 → 登记 → 刷新列表 → 直接绑定
  const onCreated = (path: string) => {
    setCreating(false)
    gw
      ?.addProject(path)
      .then((r) => {
        setProjects(r.projects ?? [])
        choose(path)
      })
      .catch(() => {})
  }

  return (
    <Field
      label="绑定项目"
      hint={value ? '飞书所有会话以此项目为工作目录' : '留空 = serve 进程当前目录（兜底，不推荐）'}
    >
      {projects.length === 0 && !value ? (
        <EmptyProjects onCreate={() => setCreating(true)} />
      ) : (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="group flex w-full items-center gap-2.5 rounded-lg border border-line bg-surface px-3 py-2 text-left outline-none transition data-[state=open]:border-primary"
            >
              <Folder size={16} className="shrink-0 text-primary" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-ink">{shown.name}</div>
                <div className="truncate font-mono text-[10.5px] text-muted-foreground">
                  {shown.path}
                </div>
              </div>
              <ChevronDown
                size={14}
                className="shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
              />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            {projects.map((p) => (
              <DropdownMenuItem key={p.path} onClick={() => choose(p.path)}>
                <Check
                  className={`text-primary ${p.path === value ? 'opacity-100' : 'opacity-0'}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-ink">{p.name}</div>
                  <div className="truncate font-mono text-[10px] text-muted-foreground">{p.path}</div>
                </div>
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => setCreating(true)} className="text-muted-foreground">
              <FolderPlus />
              新建项目
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      {creating && (
        <DirBrowser
          gw={gw}
          title="新建项目"
          onPick={onCreated}
          onCancel={() => setCreating(false)}
        />
      )}

      {/* 切换项目提醒（参考 demo A）：保存后会回收进行中的飞书会话，历史不丢 */}
      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle size={17} className="text-primary" />
              切换项目会重置飞书会话
            </DialogTitle>
            <DialogDescription className="leading-relaxed">
              保存后将<b className="text-ink">回收当前所有进行中的飞书会话</b>（群聊 / 私聊各自的常驻会话池会被重建）。正在执行的任务会被中断，但
              <b className="text-ink">历史不会丢失</b>，下条消息会在新项目目录下接着聊。
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-2 font-mono text-[11px]">
            <span className="truncate text-muted-foreground line-through">{value}</span>
            <span className="shrink-0 text-primary">→</span>
            <span className="truncate text-ink">{pending}</span>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPending(null)}>
              取消
            </Button>
            <Button
              onClick={() => {
                onChange(pending!)
                setPending(null)
              }}
            >
              确认切换
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Field>
  )
}

// 空态：该机器还没有项目，引导新建（而非手填路径）。
function EmptyProjects({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-separator px-4 py-6 text-center">
      <Folder size={28} className="text-muted-foreground/60" />
      <div className="text-sm text-ink">还没有项目</div>
      <div className="max-w-[230px] text-[11px] text-muted-foreground">
        飞书会话需要绑定一个项目作为工作目录。新建一个，或先去「项目」页登记。
      </div>
      <Button variant="outline" size="sm" onClick={onCreate} className="mt-1">
        <FolderPlus size={14} className="mr-1" />
        新建项目
      </Button>
    </div>
  )
}

function ChipEditor({ values, onChange }: { values: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const v = draft.trim()
    if (v && !values.includes(v)) onChange([...values, v])
    setDraft('')
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 mt-2">
      {values.map((v) => (
        <span key={v} className="inline-flex items-center gap-1.5 bg-surface border border-line rounded-full px-2.5 py-1 text-xs">
          {v}
          <button onClick={() => onChange(values.filter((x) => x !== v))} className="text-muted-foreground hover:text-ink">
            <X size={11} />
          </button>
        </span>
      ))}
      <span className="inline-flex items-center gap-1 bg-surface border border-dashed border-line rounded-full px-2 py-1">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && add()}
          placeholder="open_id"
          className="bg-transparent outline-none text-xs w-24 text-ink"
        />
        <button onClick={add} className="text-muted-foreground hover:text-ink">
          <Plus size={12} />
        </button>
      </span>
    </div>
  )
}

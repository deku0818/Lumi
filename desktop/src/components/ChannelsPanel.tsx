import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  Building2,
  Check,
  ChevronDown,
  ChevronRight,
  Cpu,
  Folder,
  FolderPlus,
  KeyRound,
  MessageCircle,
  Mic,
  Moon,
  Pencil,
  Plus,
  Send,
  ShieldCheck,
  X,
} from 'lucide-react'
import type {
  ChannelInfo,
  EnvInstallTarget,
  EnvProgress,
  FeishuConfig,
  CheckTone,
  DiagnoseCheck,
  Project,
  ProviderProfile,
} from '../types'
import { useEnvInstall } from './useEnvInstall'
import type { Gateway } from '../gateway'
import { MachineScope, useMachine } from './MachineTabs'
import { DirBrowser } from './DirBrowser'
import { basename, cn } from '@/lib/utils'
import {
  cardShell,
  Empty,
  EntityCard,
  Field,
  FormModal,
  GroupCard,
  Pill,
  ProgressBar,
  Section,
  SegmentedControl,
  StatusDot,
  SecretInput,
  TextInput,
  type StatusTone,
} from './SettingsKit'
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

// 渠道连接态 → 统一状态点语义：绿=已连接、金呼吸=连接中（warn+pulse）、红=失败，
// 未启用/停止为静态灰
const STATE_TONE: Record<string, StatusTone> = {
  connected: 'ok',
  connecting: 'warn',
  error: 'error',
  off: 'idle',
  stopped: 'idle',
}

const emptyFeishu = (): FeishuConfig => ({
  enabled: false,
  app_id: '',
  app_secret: '',
  allow_from: ['*'],
  group_policy: 'mention',
  model: '',
  provider: '',
  effort: 'auto',
  tool_mode: 'auto',
  workspace: '',
  minutes_enabled: false,
  daily_dream_enabled: false,
  daily_dream_time: '03:00',
  summary_max_concurrency: 3,
})

// 渠道面板（设置 → 渠道）。列表视图：各 IM 渠道卡片（状态灯 + 开关 + 编辑）；
// 表单视图：飞书配置（凭证 / 审批模式 / 群策略 / 白名单）。配置存后端 lumi.json
// （绝对路径由 get_channels 下发），保存即实时停旧起新。
export function ChannelsPanel({
  gwFor,
  active = true,
  onNavigate,
}: {
  gwFor: (id: string) => Gateway | undefined
  // 本 tab 是否可见。面板常驻挂载（保住编辑中的凭证），取数与轮询只在可见时跑
  active?: boolean
  // 体检项的修复动作不在本页时的跳转（如缺 Node.js → 设置 → 环境）。带上当前机器：
  // 体检跑在哪台机器上，就该去哪台机器的环境页装，否则装到本机而红灯照旧
  onNavigate?: (tab: string, machine: string) => void
}) {
  const [machine, setMachine] = useState('local')
  const [list, setList] = useState<ChannelInfo[]>([])
  const [providers, setProviders] = useState<ProviderProfile[]>([])
  const [editing, setEditing] = useState<FeishuConfig | null>(null) // null = 列表视图
  // 凭证落盘的绝对路径，由 get_channels 下发（渲染见 ConfigPath）
  const [configPath, setConfigPath] = useState('')

  const gw = gwFor(machine)
  const offline = useMachine(machine).scope !== 'connected'
  const reload = useCallback(() => {
    // 路径与列表恒同生共死：机器不可达（无 gateway / 请求失败）时只清列表的话，
    // 文案会变成「凭证存该机器的 <上一台机器的路径>」
    const clear = () => {
      setList([])
      setConfigPath('')
    }
    const target = gwFor(machine)
    if (!target) return clear()
    target
      .getChannels()
      .then((r) => {
        setList(r.channels ?? [])
        setConfigPath(r.config_path ?? '')
      })
      .catch(clear)
  }, [gwFor, machine])

  // 渠道连接是异步的（enable 后先 connecting 再 connected/error），可见期间轮询
  // 保持状态新鲜。本面板在别的 tab 下仍挂着（forceMount 保住编辑中的凭证），故取数
  // 一律以 active 为门——否则用户在「外观」页停留时，这里照样每 3 秒发一次 RPC。
  // 机器没连上时同样不轮：请求必然失败，只是每 3 秒把面板重渲一遍
  useEffect(() => {
    if (!active || offline) return
    reload()
    const timer = setInterval(reload, 3000)
    return () => clearInterval(timer)
  }, [active, offline, reload])

  // 该机器的供应商 profiles（渠道「模型 + 思考」配置的模型清单与思考能力来源）
  useEffect(() => {
    if (!active) return
    gwFor(machine)
      ?.listProviders()
      .then((r) => setProviders(r.profiles ?? []))
      .catch(() => setProviders([]))
  }, [active, gwFor, machine])

  const feishu = list.find((c) => c.name === 'feishu')

  const save = (config: FeishuConfig) =>
    gw
      ?.saveChannel('feishu', config)
      .then((r) => {
        setList(r.channels ?? [])
        setEditing(null)
      })
      .catch(() => {})

  // 列表开关：仅翻转 enabled 立即保存（凭证编辑走表单）。绑定项目是启用前置条件，
  // 未绑定时开关改为打开配置弹窗引导绑定——后端也会拒绝这种保存，别在这里先存一份跑不起来的启用态
  const toggleEnabled = (on: boolean) => {
    const cfg = feishu?.config ?? emptyFeishu()
    if (on && !cfg.workspace) {
      setEditing({ ...cfg, enabled: true })
      return
    }
    save({ ...cfg, enabled: on })
  }

  return (
    <div>
      <MachineScope value={machine} onChange={setMachine}>
      <Section
        title="渠道"
        desc={
          <>
            把 Lumi 接入飞书等 IM。凭证存该机器的{' '}
            <ConfigPath path={configPath} />
            （限本人可读），保存后实时重连。全程 AI 审批，仅保留 ask 询问卡片。
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
          {WECOM_CARD}
        </div>
      </Section>

      </MachineScope>

      {/* 弹窗放在作用域之外：机器瞬断（服务端重启 / 笔记本唤醒）会让作用域内的内容卸载，
          正在编辑的凭证会随之丢失——这正是 SettingsDialog 加 forceMount 要防的事 */}
      {editing && (
        <FeishuForm
          initial={editing}
          gw={gw}
          providers={providers}
          configPath={configPath}
          onNavigate={onNavigate && ((tab: string) => onNavigate(tab, machine))}
          onCancel={() => setEditing(null)}
          onSave={save}
        />
      )}
    </div>
  )
}

// 纯静态占位卡提为模块常量：本面板可见期间每 3s 轮询重渲，恒等引用让 React 跳过该子树
const WECOM_CARD = (
  <EntityCard
    dim
    icon={<Building2 size={17} />}
    title="企业微信"
    subtitle="即将支持"
    badge={<Pill dashed>即将支持</Pill>}
  />
)

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
    <EntityCard
      icon={icon}
      title={title}
      meta={
        <>
          <StatusDot tone={STATE_TONE[state] ?? 'idle'} pulse={state === 'connecting'} />
          <span
            className={`shrink-0 text-[11px] font-normal ${state === 'error' ? 'text-error' : 'text-muted-foreground'}`}
          >
            {STATUS_LABEL[state]}
          </span>
        </>
      }
      subtitle={state === 'error' ? <span className="text-error">{sub}</span> : sub}
      actions={
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onEdit}
          aria-label="编辑"
          className="text-muted-foreground"
        >
          <Pencil />
        </Button>
      }
      trailing={<Switch checked={enabled} onCheckedChange={onToggle} />}
    />
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
  configPath,
  onNavigate,
  onCancel,
  onSave,
}: {
  initial: FeishuConfig
  gw?: Gateway
  providers: ProviderProfile[]
  configPath: string // 凭证落盘的绝对路径（空 = 尚未取到，文案退回不带路径的说法）
  onNavigate?: (tab: string) => void
  onCancel: () => void
  onSave: (cfg: FeishuConfig) => void
}) {
  const [cfg, setCfg] = useState<FeishuConfig>(initial)
  const set = (patch: Partial<FeishuConfig>) => setCfg((c) => ({ ...c, ...patch }))

  // 接入体检：权限 / 事件订阅 / 版本发布任缺其一，机器人都是「连上了但不回消息」，
  // 且开放平台不报任何错。四项由一次「应用版本信息」查询判定。
  const setup = useDiagnose(() =>
    gw?.diagnoseFeishuSetup('feishu', {
      app_id: cfg.app_id,
      app_secret: cfg.app_secret,
      workspace: cfg.workspace,
    }),
  )
  // 妙记体检：走 lark-cli 子进程 + 网络，耗时 1-2s
  const minutes = useDiagnose(() => gw?.diagnoseMinutes('feishu', { app_id: cfg.app_id }))

  // 就地修复（一键安装）：进度显示在对应检查行，本渠道相关安装结束后重跑体检
  const { progress: fixProgress, install: fixInstall, seed } = useEnvInstall(gw, {
    targets: ['lark-cli', 'feishu-skills'],
    onState: () => setup.run(),
  })
  const onFix = (action: EnvInstallTarget) => fixInstall(action, cfg.workspace)

  useEffect(() => {
    if (initial.minutes_enabled) minutes.run()
    // 打开时若已有本渠道相关的安装在跑（弹窗关过重开），恢复进行中态
    gw?.envStatus().then((s) => {
      if (s.installing === 'lark-cli' || s.installing === 'feishu-skills') seed(s.installing)
    }).catch(() => {})
    // 只在弹窗打开时查一次；凭证改动后靠「重新检查」手动触发，输入途中不打扰
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 换绑定项目即换技能包检测/安装落点，体检跟着刷新。打开时也经此跑首查
  // （本地环境两项不依赖凭证；无凭证时远程侧报「缺少凭证」同样是信息）
  useEffect(() => {
    setup.run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg.workspace])

  const allowAll = cfg.allow_from.includes('*')

  // 没有独立的「测试连接」：接入体检第①项已验凭证，且信息更全。两者并存会矛盾
  // ——凭证对但缺 app_version 权限时，bot/v3/info 报成功而体检报失败
  //
  // 未绑定项目不给保存：飞书会话必须有明确的工作目录，没有「用 serve 进程目录」这条退路
  const footer = (
    <>
      {!cfg.workspace && <div className="text-[11px] text-error">请先绑定项目</div>}
      <div className="flex-1" />
      <Button variant="ghost" onClick={onCancel}>
        取消
      </Button>
      <Button disabled={!cfg.workspace} onClick={() => onSave(cfg)}>
        保存并重连
      </Button>
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
      <div className="space-y-3">
        <GroupCard
          icon={KeyRound}
          title="应用凭证"
          desc={
            <>
              chmod 600 存 <ConfigPath path={configPath} />
              ，不写入项目目录
            </>
          }
        >
          <div className="grid grid-cols-2 gap-4">
            <Field label="App ID" hint="支持 ${FEISHU_APP_ID} 引用环境变量">
              <TextInput value={cfg.app_id} onChange={(e) => set({ app_id: e.target.value })} placeholder="cli_…" />
            </Field>
            <Field label="App Secret">
              <SecretInput value={cfg.app_secret} onChange={(e) => set({ app_secret: e.target.value })} placeholder="●●●●" />
            </Field>
          </div>
          {/* 绑定项目归凭证组：它是体检的输入——技能包按此项目检测与安装，所见即所得 */}
          <WorkspacePicker gw={gw} value={cfg.workspace} onChange={(v) => set({ workspace: v })} />
        </GroupCard>

        <GroupCard
          icon={ShieldCheck}
          title="接入体检"
          desc="本地环境 / 权限 / 事件订阅 / 版本发布，缺任一机器人都收不到消息且开放平台不报错"
        >
          <CheckPanel
            key={panelKey(setup.checks)}
            {...setup}
            subject="机器人接入"
            ready="已就绪 · 可正常收发消息"
            onFix={onFix}
            onNavigate={onNavigate}
            fixProgress={fixProgress}
          />
        </GroupCard>

        <GroupCard icon={MessageCircle} title="消息行为" desc="谁能唤起 Lumi、在群里何时回应">
          <div className="grid grid-cols-2 gap-4">
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
            </Field>
          </div>
          {!allowAll && (
            <ChipEditor values={cfg.allow_from} onChange={(vals) => set({ allow_from: vals })} />
          )}
        </GroupCard>

        <GroupCard icon={Cpu} title="会话运行时" desc="这个渠道的 Agent 用什么模型、怎么思考、怎么审批">
          <ChannelRuntimeFields cfg={cfg} set={set} providers={providers} />
        </GroupCard>

        <GroupCard
          icon={Mic}
          title="妙记纪要"
          desc="录音 / 会议结束后自动整理纪要，推送到私聊"
          open={cfg.minutes_enabled}
          action={
            <Switch
              checked={cfg.minutes_enabled}
              onCheckedChange={(on) => {
                set({ minutes_enabled: on })
                // 刚打开开关时立刻体检，省得用户还要手点一次「检查」
                if (on && !minutes.checks) minutes.run()
              }}
            />
          }
        >
          <CheckPanel
            key={panelKey(minutes.checks)}
            {...minutes}
            subject="妙记链路"
            ready="已就绪 · 妙记生成后自动推送纪要"
          />
        </GroupCard>

        <GroupCard
          icon={Moon}
          title="每日记忆整理（Dream）"
          desc="到点自动沉淀记忆 + 压缩会话，长会话不再无限膨胀"
          open={cfg.daily_dream_enabled}
          action={
            <Switch
              checked={cfg.daily_dream_enabled}
              onCheckedChange={(on) => set({ daily_dream_enabled: on })}
            />
          }
          bodyClassName="grid grid-cols-2 gap-4"
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
        </GroupCard>
      </div>
    </FormModal>
  )
}

// 每轮新结果重新挂载：展开态回到「异常自动展开」，无需 effect 重置
const panelKey = (checks: DiagnoseCheck[] | null) =>
  checks?.map((c) => `${c.key}${c.tone}`).join() ?? 'none'

// 凭证落盘路径：后端给绝对路径（前端拼 ~/.lumi 既看不懂，--config-dir 时还会说谎），
// 尚未取到时退回一句不带路径的说法。两处文案共用一份，免得各写各的措辞
function ConfigPath({ path }: { path: string }) {
  return path ? <code className="break-all">{path}</code> : <>本机配置文件</>
}

// Check.fix_nav 的取值 → 设置面板名。检查行是各链路共用的，标签只认 tab、不认具体检查
const TAB_LABEL: Record<string, string> = { env: '环境' }

// 逐项体检面板（demo 方案 A「整卡开合」）：机器人接入与妙记链路共用。详情装在卡内
// ——头行整行可点、chevron 指示开合，卡顶 2px 色线 + 头行状态点表达三色语义（tint 只
// 表达状态，不再当容器底色）。收起时缩略点阵（每项一粒）保留全貌；异常自动展开
// （panelKey 重挂实现），之后随用户手动开合。
// subject 派生出「正在检查 X…」「检查 X」，只有就绪语（ready）各链路不同
function CheckPanel({
  checks,
  loading,
  error,
  run,
  subject,
  ready,
  onFix,
  onNavigate,
  fixProgress,
}: ReturnType<typeof useDiagnose> & {
  subject: string
  ready: string
  // 就地修复：check.fix_action 非空时渲染一键安装按钮 / 进行中的行内进度
  onFix?: (action: EnvInstallTarget) => void
  // 跳转修复：check.fix_nav 非空时渲染「去那个面板」的按钮
  onNavigate?: (tab: string) => void
  fixProgress?: Record<string, EnvProgress>
}) {
  const bad = checks?.filter((c) => c.tone === 'error') ?? []
  const warned = checks?.filter((c) => c.tone === 'warn') ?? []
  // 有问题就默认展开——用户不该为了知道哪里坏了还多点一次；之后随用户手动开合
  const [open, setOpen] = useState(bad.length > 0)

  if (loading)
    return (
      <DiagShell bar="bg-primary/30">
        <div className={`${diagRow} text-muted-foreground`}>
          <span className="lumi-orb" style={{ width: 11, height: 11 }} />
          正在检查{subject}…
        </div>
      </DiagShell>
    )

  // 体检没跑成时说清楚为什么，而不是退回「没检查过」的样子让用户空点
  if (error)
    return (
      <DiagShell bar="bg-error/60">
        <div className={diagRow}>
          <StatusDot tone="error" />
          <span className="flex-1 text-error">体检未能执行：{error}</span>
          <button onClick={run} className="text-[11px] text-muted-foreground hover:text-ink">
            重试
          </button>
        </div>
      </DiagShell>
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
    <DiagShell bar={TONE[tone].bar}>
      {/* 头行是 div 而非 button：内含「重新检查」与 chevron 两个真按钮（button 嵌套非法）。
          鼠标点行内任意空白也能开合；键盘走 chevron 按钮（带 aria-expanded） */}
      <div
        onClick={() => setOpen((v) => !v)}
        className={`${diagRow} w-full cursor-pointer transition hover:bg-line/20`}
      >
        <StatusDot tone={tone} />
        <span className={`flex-1 min-w-0 truncate ${TONE[tone].text}`}>
          {tone === 'error'
            ? `${bad.length} 项未就绪：${bad[0].name}`
            : tone === 'warn'
              ? `${ready}，${warned.length} 项功能降级`
              : ready}
        </span>
        {/* 缩略点阵：收起时也能一眼看到每项检查各自的红绿 */}
        <span className="flex gap-1 shrink-0">
          {checks.map((c) => (
            <i key={c.key} className={`size-[5px] rounded-full ${TONE[c.tone].mini}`} />
          ))}
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation()
            run()
          }}
          className="shrink-0 text-[11px] text-muted-foreground hover:text-ink"
        >
          重新检查
        </button>
        <button
          aria-expanded={open}
          aria-label={open ? '收起检查详情' : '查看检查详情'}
          onClick={(e) => {
            e.stopPropagation()
            setOpen((v) => !v)
          }}
          className="shrink-0 grid place-items-center text-muted-foreground hover:text-ink"
        >
          <ChevronRight
            size={13}
            className={`transition-transform ${open ? 'rotate-90' : ''}`}
          />
        </button>
      </div>
      {/* grid-rows 0fr→1fr：不量高度的展开动画。收起时 inert：内容保持挂载以便动画，
          但隐形的修复按钮/链接不能留在 Tab 序与命中区里 */}
      <div
        inert={!open}
        className={`grid transition-[grid-template-rows] duration-200 ${open ? '[grid-template-rows:1fr]' : '[grid-template-rows:0fr]'}`}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="border-t border-line/50 px-3 pb-2.5">
            {checks.map((c, i) => (
              <div key={c.key}>
                {/* 分组标签：仅在组名与上一项不同处插入（本地环境 / 机器人接入） */}
                {c.group && c.group !== checks[i - 1]?.group && (
                  <div className="flex items-center gap-2 pt-2 pb-0.5 text-[10.5px] tracking-wide text-muted-foreground">
                    {c.group}
                    <span className="h-px flex-1 bg-line/45" />
                  </div>
                )}
                <CheckRow
                  check={c}
                  onFix={onFix}
                  onNavigate={onNavigate}
                  progress={fixProgress?.[c.fix_action]}
                />
              </div>
            ))}
          </div>
        </div>
      </div>
    </DiagShell>
  )
}

// 体检卡外壳：三个状态（检查中 / 未能执行 / 结果）共用「卡壳 + 2px 状态色顶条」，
// 头行行高与内边距由 diagRow 统一——免得三处 chrome 各自漂移
function DiagShell({ bar, children }: { bar: string; children: React.ReactNode }) {
  return (
    <div className={cn(cardShell, 'overflow-hidden')}>
      <div className={`h-0.5 ${bar}`} />
      {children}
    </div>
  )
}
const diagRow = 'flex items-center gap-2.5 px-3 py-2.5 text-xs'

// 三色语义的成套配色（顶条 / 头行文字 / 缩略小点）；语义本身由后端定（DiagnoseCheck.tone）。
// warn 文字刻意用 ink 而非金：头行是整句话，金字大段可读性差。点本体恒走 kit 的 StatusDot
const TONE: Record<CheckTone, { bar: string; text: string; mini: string }> = {
  ok: { bar: 'bg-success/55', text: 'text-success', mini: 'bg-success' },
  warn: { bar: 'bg-primary/60', text: 'text-ink', mini: 'bg-primary' },
  error: { bar: 'bg-error/60', text: 'text-error', mini: 'bg-error' },
}

function CheckRow({
  check,
  onFix,
  onNavigate,
  progress,
}: {
  check: DiagnoseCheck
  onFix?: (action: EnvInstallTarget) => void
  onNavigate?: (tab: string) => void
  progress?: EnvProgress
}) {
  // const 收窄让闭包里也保持 EnvInstallTarget 类型（直接用 check.fix_action 在回调内不收窄）
  const fixAction = check.fix_action || null
  return (
    <div className="flex items-start gap-2.5 py-1.5 border-t border-line/55 first:border-t-0">
      <span className="mt-1.5">
        <StatusDot tone={progress ? 'warn' : check.tone} />
      </span>
      <div className="flex-1 min-w-0">
        <div className={check.tone === 'error' && !progress ? 'text-xs text-error' : 'text-xs'}>{check.name}</div>
        {progress && <ProgressBar progress={progress} className="mt-1" />}
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
        {/* 修复入口不在本页（如缺 Node.js）：送到唯一的那个入口，不在此复制一份安装按钮。
            标签取自 tab 名而非某一条检查的内容——这行是各条链路共用的，具体做什么由
            后端写在 detail / fix_note 里（同 fix_url 的「去开放平台配置」） */}
        {check.fix_nav && onNavigate && TAB_LABEL[check.fix_nav] && (
          <button
            onClick={() => onNavigate(check.fix_nav)}
            className="mt-1.5 inline-flex items-center gap-1 rounded-lg border border-primary/40 bg-primary/10 px-2.5 py-1 text-[11px] text-primary hover:bg-primary/20"
          >
            去「{TAB_LABEL[check.fix_nav]}」→
          </button>
        )}
      </div>
      {/* 就地修复按钮：一键安装（cli / 技能包），进行中由上方进度条替代 */}
      {fixAction && check.tone !== 'ok' && !progress && onFix && (
        <Button size="sm" className="mt-0.5 h-6 px-2.5 text-[11px]" onClick={() => onFix(fixAction)}>
          一键安装
        </Button>
      )}
    </div>
  )
}

// 档位显示名（对齐 ModelPicker）：auto→自动 / on→On / 其余首字母大写
const levelLabel = (lv: string) =>
  lv === 'auto' ? '自动' : lv === 'on' ? 'On' : lv.charAt(0).toUpperCase() + lv.slice(1)

// 渠道「会话运行时」通用块：模型 + 思考档位 + 工具审批。各 IM 渠道复用同一块
// （对齐后端 ChannelRuntimeConfig），值各渠道各存一份。model 空 = 跟随 desktop 全局。
// 绑定项目已前置到凭证组（它是接入体检的输入），不在此块；卡壳由调用方的 GroupCard 提供。
function ChannelRuntimeFields({
  cfg,
  set,
  providers,
}: {
  cfg: FeishuConfig
  set: (patch: Partial<FeishuConfig>) => void
  providers: ProviderProfile[]
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
  const firstProv = providers.find((p) => p.models.length)
  const firstModel = firstProv?.models[0] ?? ''
  const cap = custom ? capOf(cfg.model) : undefined
  const control = cap?.control ?? 'none'

  return (
    <>
      <Field
        label="模型来源"
        hint="跟随全局＝用 desktop 当前模型；指定＝本渠道独立，与 desktop 互不影响"
      >
        <SegmentedControl
          value={custom ? 'custom' : 'global'}
          onChange={(v) => {
            setSource(v as 'global' | 'custom')
            if (v === 'global') set({ model: '', provider: '', effort: 'auto' })
            // 切「指定」时有可用模型就补第一个；providers 未加载完（firstModel=''）则
            // 先进 custom 视图，由模型下拉待选，加载后即可选
            else if (!cfg.model && firstModel)
              set({ model: firstModel, provider: firstProv?.id ?? '', effort: 'auto' })
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
            provider={cfg.provider}
            onPick={(m, pid) => set({ model: m, provider: pid, effort: coerce(m, cfg.effort) })}
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
    </>
  )
}

// 模型下拉：按 provider 分组列出所有模型（对齐 ModelPicker 的 More models 子菜单）。
// 选中态按 (provider, model) 判定——同名模型可存在于多个连接，只按名会两处都打勾。
function ModelDropdown({
  providers,
  value,
  provider,
  onPick,
}: {
  providers: ProviderProfile[]
  value: string
  provider: string // 空 = 老配置未记录归属，回退按名找第一处
  onPick: (m: string, providerId: string) => void
}) {
  const prov =
    providers.find((p) => p.id === provider && p.models.includes(value)) ??
    providers.find((p) => p.models.includes(value))
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
              <DropdownMenuItem key={m} onClick={() => onPick(m, p.id)}>
                <Check
                  className={`text-primary ${m === value && p.id === prov?.id ? 'opacity-100' : 'opacity-0'}`}
                />
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
// 必选、无兜底：未绑定则保存按钮禁用，后端也拒绝启用（不退回 serve 进程目录）。
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
  const [addErr, setAddErr] = useState('') // 新建项目登记失败原因（否则失败无声，看着像没反应）

  useEffect(() => {
    gw
      ?.listProjects()
      .then((r) => setProjects(r.projects ?? []))
      .catch(() => setProjects([]))
  }, [gw])

  const current = projects.find((p) => p.path === value)
  const bound = !!value
  // 触发器展示：已登记取项目名 / 仅有路径取 basename / 未绑定 = 红字催选（不是某个兜底目录）
  const shown = current
    ? { name: current.name, path: value }
    : bound
      ? { name: basename(value), path: value }
      : { name: '未绑定项目', path: '点此选择一个项目作为飞书工作目录' }

  // 选中项目：与当前不同且已有绑定 → 弹确认；否则直接生效
  const choose = (path: string) => {
    if (path === value) return
    if (value) setPending(path)
    else onChange(path)
  }

  // 新建项目：浏览目录 → 登记 → 刷新列表 → 直接绑定。登记失败要说出来——
  // 否则弹窗一关什么都没变，用户以为已经绑上了
  const onCreated = (path: string) => {
    setCreating(false)
    setAddErr('')
    gw
      ?.addProject(path)
      .then((r) => {
        setProjects(r.projects ?? [])
        choose(path)
      })
      .catch((e) => setAddErr(String(e?.message || e) || '登记项目失败'))
  }

  return (
    <Field
      label="绑定项目（必选）"
      hint={
        value
          ? '飞书所有会话以此项目为工作目录；飞书技能包也安装到它的 .lumi/skills/'
          : '必选：未绑定项目的飞书渠道不会启动，也没有「用 serve 进程目录」的兜底'
      }
    >
      {projects.length === 0 && !value ? (
        <EmptyProjects onCreate={() => setCreating(true)} />
      ) : (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={`group flex w-full items-center gap-2.5 rounded-lg border bg-surface px-3 py-2 text-left outline-none transition data-[state=open]:border-primary ${bound ? 'border-line' : 'border-error/60'}`}
            >
              <Folder size={16} className={`shrink-0 ${bound ? 'text-primary' : 'text-error'}`} />
              <div className="min-w-0 flex-1">
                <div className={`truncate text-sm ${bound ? 'text-ink' : 'text-error'}`}>{shown.name}</div>
                <div className={`truncate text-[10.5px] text-muted-foreground ${bound ? 'font-mono' : ''}`}>
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

      {addErr && <div className="mt-1.5 text-[11px] text-error">{addErr}</div>}

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

// 空态：该机器还没有项目，引导新建（而非手填路径）。外壳走统一 Empty（虚线框）。
function EmptyProjects({ onCreate }: { onCreate: () => void }) {
  return (
    <Empty>
      <div className="flex flex-col items-center gap-2">
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
    </Empty>
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

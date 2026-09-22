import { memo, useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  AlertTriangle,
  Building2,
  ChevronRight,
  Cpu,
  KeyRound,
  MessageCircle,
  Mic,
  Moon,
  Pencil,
  Plus,
  Send,
  ShieldCheck,
} from 'lucide-react'
import type {
  ChannelInfo,
  EnvInstallTarget,
  EnvProgress,
  FeishuConfig,
  CheckTone,
  DiagnoseCheck,
} from '../types'
import { useEnvInstall } from './useEnvInstall'
import type { Gateway } from '../gateway'
import { useI18n, type Translate } from '../i18n'
import { ConfirmDialog } from './ConfirmDialog'
import { MachineScope, useMachine } from './MachineTabs'
import { ProjectPicker } from './ProjectPicker'
import { basename, cn, errorMessage } from '@/lib/utils'
import {
  ChipInput,
  EntityCard,
  Field,
  FormModal,
  GroupCard,
  Loading,
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
import { CARD_L2 } from './glass'

// 文案里 **…** 段加粗（确认弹窗里用户唯一需据以决策的信息）
const emphasize = (s: string): ReactNode =>
  s.split('**').map((seg, i) => (i % 2 ? <b key={i} className="text-ink">{seg}</b> : seg))

// 渠道连接态 → 统一状态点语义：绿=已连接、金呼吸=连接中（warn+pulse）、红=失败，
// 未启用/停止为静态灰
const STATE_TONE: Record<string, StatusTone> = {
  connected: 'ok',
  connecting: 'warn',
  error: 'error',
  off: 'idle',
  stopped: 'idle',
}

const emptyFeishu = (t: Translate): FeishuConfig => ({
  id: '', // 空 = 新建（后端保存时生成）
  name: t('channels.feishuBot'),
  enabled: false,
  app_id: '',
  app_secret: '',
  allow_from: ['*'],
  group_policy: 'mention',
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
  const { t } = useI18n()
  const [machine, setMachine] = useState('local')
  const [list, setList] = useState<ChannelInfo[]>([])
  const [editing, setEditing] = useState<FeishuConfig | null>(null) // null = 列表视图
  // 最近一次保存/删除的后端拒绝原因（App ID 撞已有机器人等）：吞掉会让保存键看似失灵
  const [saveError, setSaveError] = useState('')
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

  // 一台机器多个机器人：每个飞书机器人一条（config.id 区分）
  const feishuBots = list.filter((c) => c.name === 'feishu')
  // 其他机器人已占用的项目（workspace → 机器人名）：新建/改绑时置灰并说明被谁占用
  const takenWorkspaces = (excludeId: string) =>
    new Map(
      feishuBots
        .filter((c) => c.config.id !== excludeId && c.config.workspace)
        .map((c) => [c.config.workspace, c.config.name]),
    )

  // save/remove 共用收尾：成功刷列表关弹窗清错误，失败把后端拒绝原因亮出来
  const settle = (p?: Promise<{ channels?: ChannelInfo[] }>) =>
    p
      ?.then((r) => {
        setList(r.channels ?? [])
        setEditing(null)
        setSaveError('')
      })
      .catch((e) => setSaveError(errorMessage(e)))

  const save = (config: FeishuConfig) => settle(gw?.saveChannel('feishu', config))
  const remove = (botId: string) => settle(gw?.deleteChannel('feishu', botId))

  // 列表开关：仅翻转 enabled 立即保存（凭证编辑走表单）。绑定项目是启用前置条件，
  // 未绑定时开关改为打开配置弹窗引导绑定——后端也会拒绝这种保存，别在这里先存一份跑不起来的启用态
  const toggleEnabled = (cfg: FeishuConfig, on: boolean) => {
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
        title={t('settings.channels')}
        desc={
          <>
            {t('channels.desc1')}
            <ConfigPath path={configPath} />
            {t('channels.desc2')}
          </>
        }
      >
        <div className="space-y-2">
          {/* 飞书机器人：每个项目可配一个，一行一个 */}
          {feishuBots.map((c) => (
            <ChannelCard
              key={c.config.id}
              title={c.config.name || t('channels.feishuBot')}
              status={c.status}
              enabled={c.enabled}
              subtitle={feishuSubtitle(c, t)}
              onToggle={(on) => toggleEnabled(c.config, on)}
              onEdit={() => setEditing(c.config)}
            />
          ))}
          {/* 新建机器人（每个项目一个，项目在表单里选，已占用的置灰） */}
          <button
            onClick={() => setEditing(emptyFeishu(t))}
            className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-dashed border-separator px-3 py-2.5 text-xs text-muted-foreground transition hover:border-muted-foreground hover:text-ink"
          >
            <Plus size={13} />
            {t('channels.newBot')}
          </button>
          {/* 列表态操作（开关翻转等）被后端拒绝时的原因；表单内的错误在弹窗脚部显示 */}
          {saveError && !editing && (
            <div className="text-[11px] text-error">{saveError}</div>
          )}

          {/* 企业微信（即将支持） */}
          <WecomCard />
        </div>
      </Section>

      </MachineScope>

      {/* 弹窗放在作用域之外：机器瞬断（服务端重启 / 笔记本唤醒）会让作用域内的内容卸载，
          正在编辑的凭证会随之丢失——这正是 SettingsDialog 加 forceMount 要防的事 */}
      {editing && (
        <FeishuForm
          initial={editing}
          gw={gw}
          machine={machine}
          configPath={configPath}
          taken={takenWorkspaces(editing.id)}
          saveError={saveError}
          onNavigate={onNavigate && ((tab: string) => onNavigate(tab, machine))}
          onCancel={() => {
            setEditing(null)
            setSaveError('')
          }}
          onSave={save}
          onDelete={editing.id ? () => remove(editing.id) : undefined}
        />
      )}
    </div>
  )
}

// 纯静态占位卡 memo：本面板可见期间每 3s 轮询重渲，无 props 让 React 跳过该子树
const WecomCard = memo(function WecomCard() {
  const { t } = useI18n()
  return (
    <EntityCard
      dim
      icon={<Building2 size={17} />}
      title={t('channels.wecom')}
      subtitle={t('channels.comingSoon')}
      badge={<Pill dashed>{t('channels.comingSoon')}</Pill>}
    />
  )
})

function feishuSubtitle(c: ChannelInfo, t: Translate): string {
  // 项目是机器人的身份归属，未启用也要能一眼分清哪条是哪个项目的
  const proj = c.config.workspace ? basename(c.config.workspace) : t('channels.unbound')
  if (!c.enabled) return `${proj} · ${t('channels.status.off')}`
  const mode = t(c.config.tool_mode === 'auto' ? 'chan.mode.auto' : 'chan.mode.privileged')
  const who = c.config.allow_from.includes('*')
    ? t('channels.allowAll')
    : t('channels.allowN', { n: c.config.allow_from.length })
  return `${proj} · ${mode} · ${who}`
}

// 飞书机器人行：状态点 + 状态字 + 副题（error 态换成后端给的具体原因）+ 编辑 + 开关
function ChannelCard({
  title,
  status,
  enabled,
  subtitle,
  onToggle,
  onEdit,
}: {
  title: string
  status?: { state: string; detail: string }
  enabled: boolean
  subtitle: string
  onToggle: (on: boolean) => void
  onEdit: () => void
}) {
  const { t } = useI18n()
  const state = status?.state ?? 'off'
  const sub = state === 'error' && status?.detail ? status.detail : subtitle
  return (
    <EntityCard
      icon={<Send size={17} />}
      title={title}
      meta={
        <>
          <StatusDot tone={STATE_TONE[state] ?? 'idle'} pulse={state === 'connecting'} />
          <span
            className={`shrink-0 text-[11px] font-normal ${state === 'error' ? 'text-error' : 'text-muted-foreground'}`}
          >
            {t(`channels.status.${state}`)}
          </span>
        </>
      }
      subtitle={state === 'error' ? <span className="text-error">{sub}</span> : sub}
      actions={
        <Button variant="ghost" size="icon-sm" onClick={onEdit} aria-label={t('common.edit')} className="text-muted-foreground">
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
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoading(false))
  }
  return { checks, loading, error, run }
}

function FeishuForm({
  initial,
  gw,
  machine,
  configPath,
  taken,
  saveError,
  onNavigate,
  onCancel,
  onSave,
  onDelete,
}: {
  initial: FeishuConfig
  gw?: Gateway
  machine: string
  configPath: string // 凭证落盘的绝对路径（空 = 尚未取到，文案退回不带路径的说法）
  taken: Map<string, string> // 其他机器人已占用的项目（workspace → 机器人名），1:1 约束
  saveError?: string // 后端拒绝保存/删除的原因（App ID 撞已有机器人等）
  onNavigate?: (tab: string) => void
  onCancel: () => void
  onSave: (cfg: FeishuConfig) => void
  onDelete?: () => void // 仅已保存的机器人可删（新建表单没有这条路）
}) {
  const { t } = useI18n()
  const [cfg, setCfg] = useState<FeishuConfig>(initial)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const set = (patch: Partial<FeishuConfig>) => setCfg((c) => ({ ...c, ...patch }))

  // 接入体检：权限 / 事件订阅 / 版本发布任缺其一，机器人都是「连上了但不回消息」，
  // 且开放平台不报任何错。四项由一次「应用版本信息」查询判定。
  const setup = useDiagnose(() =>
    gw?.diagnoseFeishuSetup('feishu', {
      id: cfg.id, // 体检「lark-cli 身份」项按机器人 profile（cli_profile）判定
      cli_profile: cfg.cli_profile,
      app_id: cfg.app_id,
      app_secret: cfg.app_secret,
      workspace: cfg.workspace,
    }),
  )
  // 妙记体检：走 lark-cli 子进程 + 网络，耗时 1-2s（用户授权按机器人 profile 各自独立）
  const minutes = useDiagnose(() =>
    gw?.diagnoseMinutes('feishu', {
      id: cfg.id,
      cli_profile: cfg.cli_profile,
      app_id: cfg.app_id,
    }),
  )

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
      {onDelete && (
        <Button
          variant="ghost"
          className="text-error hover:text-error"
          onClick={() => setConfirmDelete(true)}
        >
          {t('common.delete')}
        </Button>
      )}
      {!cfg.workspace && <div className="text-[11px] text-error">{t('channels.bindFirst')}</div>}
      {saveError && (
        <div className="min-w-0 truncate text-[11px] text-error" title={saveError}>
          {saveError}
        </div>
      )}
      <div className="flex-1" />
      <Button variant="ghost" onClick={onCancel}>
        {t('common.cancel')}
      </Button>
      <Button disabled={!cfg.workspace} onClick={() => onSave(cfg)}>
        {t('channels.saveReconnect')}
      </Button>
    </>
  )

  return (
    <FormModal
      onClose={onCancel}
      title={cfg.id ? cfg.name || t('channels.feishuBot') : t('channels.newBot')}
      footer={footer}
      className="sm:max-w-2xl"
      bodyClassName="max-h-[66vh]"
    >
      <div className="space-y-3">
        <GroupCard
          icon={KeyRound}
          title={t('channels.credentials')}
          desc={
            <>
              {t('channels.credDesc1')}
              <ConfigPath path={configPath} />
              {t('channels.credDesc2')}
            </>
          }
        >
          <Field label={t('channels.botName')} hint={t('channels.botNameHint')}>
            <TextInput
              value={cfg.name}
              onChange={(e) => set({ name: e.target.value })}
              placeholder={t('channels.feishuBot')}
            />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="App ID" hint={t('channels.appIdHint')}>
              <TextInput value={cfg.app_id} onChange={(e) => set({ app_id: e.target.value })} placeholder="cli_…" />
            </Field>
            <Field label="App Secret">
              <SecretInput value={cfg.app_secret} onChange={(e) => set({ app_secret: e.target.value })} placeholder="●●●●" />
            </Field>
          </div>
          {/* 绑定项目归凭证组：它是体检的输入——技能包按此项目检测与安装，所见即所得。
              必选、无兜底：未绑定则保存按钮禁用，后端也拒绝启用；切换已绑定项目会弹重置提醒 */}
          <Field
            label={t('channels.bindProject')}
            hint={t(cfg.workspace ? 'channels.bindHintBound' : 'channels.bindHintUnbound')}
          >
            <ProjectPicker
              gw={gw}
              machine={machine}
              value={cfg.workspace}
              onChange={(v) => set({ workspace: v })}
              taken={taken}
              required
              confirmSwitch={{
                title: t('channels.switchTitle'),
                message: () => emphasize(t('channels.switchMessage')),
                confirmLabel: t('channels.switchConfirm'),
              }}
            />
          </Field>
        </GroupCard>

        <GroupCard icon={ShieldCheck} title={t('channels.setupCheck')} desc={t('channels.setupCheckDesc')}>
          <CheckPanel
            key={panelKey(setup.checks)}
            {...setup}
            subject={t('channels.setupSubject')}
            ready={t('channels.setupReady')}
            onFix={onFix}
            onNavigate={onNavigate}
            fixProgress={fixProgress}
          />
        </GroupCard>

        <GroupCard icon={MessageCircle} title={t('channels.behavior')} desc={t('channels.behaviorDesc')}>
          <div className="grid grid-cols-2 gap-4">
            <Field label={t('channels.groupPolicy')}>
              <SegmentedControl
                value={cfg.group_policy}
                onChange={(v) => set({ group_policy: v as FeishuConfig['group_policy'] })}
                options={[
                  { val: 'mention', label: t('channels.policyMention') },
                  { val: 'open', label: t('channels.policyOpen') },
                ]}
              />
            </Field>
            <Field label={t('channels.allowList')} hint={t(allowAll ? 'channels.allowAll' : 'channels.allowListHint')}>
              <SegmentedControl
                value={allowAll ? 'all' : 'list'}
                onChange={(v) => set({ allow_from: v === 'all' ? ['*'] : [] })}
                options={[
                  { val: 'all', label: t('channels.everyone') },
                  { val: 'list', label: t('channels.members') },
                ]}
              />
            </Field>
          </div>
          {!allowAll && (
            <ChipInput unique className="mt-2" values={cfg.allow_from} onChange={(vals) => set({ allow_from: vals })} placeholder="open_id" />
          )}
        </GroupCard>

        <GroupCard icon={Cpu} title={t('channels.runtime')} desc={t('channels.runtimeDesc')}>
          <Field label={t('approval.label')} hint={t('channels.toolModeHint')}>
            <SegmentedControl
              value={cfg.tool_mode}
              onChange={(v) => set({ tool_mode: v as FeishuConfig['tool_mode'] })}
              options={[
                { val: 'auto', label: t('chan.mode.auto') },
                { val: 'privileged', label: t('chan.mode.privileged') },
              ]}
            />
          </Field>
        </GroupCard>

        <GroupCard
          icon={Mic}
          title={t('channels.minutes')}
          desc={t('channels.minutesDesc')}
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
            subject={t('channels.minutesSubject')}
            ready={t('channels.minutesReady')}
          />
        </GroupCard>

        <GroupCard
          icon={Moon}
          title={t('channels.dream')}
          desc={t('channels.dreamDesc')}
          open={cfg.daily_dream_enabled}
          action={
            <Switch
              checked={cfg.daily_dream_enabled}
              onCheckedChange={(on) => set({ daily_dream_enabled: on })}
            />
          }
          bodyClassName="grid grid-cols-2 gap-4"
        >
          <Field label={t('channels.dreamTime')} hint={t('channels.dreamTimeHint')}>
            <TextInput
              type="time"
              value={cfg.daily_dream_time}
              onChange={(e) => set({ daily_dream_time: e.target.value })}
            />
          </Field>
          <Field label={t('channels.summaryConcurrency')} hint={t('channels.summaryConcurrencyHint')}>
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

      {/* 删除确认：连同其 lark-cli 专属身份与进行中的会话池一并回收，聊天历史不删 */}
      {confirmDelete && (
        <ConfirmDialog
          icon={<AlertTriangle size={17} className="text-error" />}
          title={t('channels.deleteTitle', { name: cfg.name || t('channels.feishuBot') })}
          message={emphasize(t('channels.deleteMessage'))}
          onConfirm={() => onDelete?.()}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </FormModal>
  )
}

// 每轮新结果重新挂载：展开态回到「异常自动展开」，无需 effect 重置
const panelKey = (checks: DiagnoseCheck[] | null) =>
  checks?.map((c) => `${c.key}${c.tone}`).join() ?? 'none'

// 凭证落盘路径：后端给绝对路径（前端拼 ~/.lumi 既看不懂，--config-dir 时还会说谎），
// 尚未取到时退回一句不带路径的说法。两处文案共用一份，免得各写各的措辞
function ConfigPath({ path }: { path: string }) {
  const { t } = useI18n()
  return path ? <code className="break-all">{path}</code> : <>{t('channels.localConfig')}</>
}

// Check.fix_nav 的取值 → 设置面板名的 i18n key。检查行是各链路共用的，标签只认 tab、不认具体检查
const TAB_LABEL: Record<string, string> = { env: 'settings.env' }

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
  const { t } = useI18n()
  const bad = checks?.filter((c) => c.tone === 'error') ?? []
  const warned = checks?.filter((c) => c.tone === 'warn') ?? []
  // 有问题就默认展开——用户不该为了知道哪里坏了还多点一次；之后随用户手动开合
  const [open, setOpen] = useState(bad.length > 0)

  if (loading)
    return (
      <DiagShell bar="bg-primary/30">
        <div className={`${diagRow} text-muted-foreground`}>
          <Loading />
          {t('channels.checking', { subject })}
        </div>
      </DiagShell>
    )

  // 体检没跑成时说清楚为什么，而不是退回「没检查过」的样子让用户空点
  if (error)
    return (
      <DiagShell bar="bg-error/60">
        <div className={diagRow}>
          <StatusDot tone="error" />
          <span className="flex-1 text-error">{t('channels.diagError', { error })}</span>
          <button onClick={run} className="text-[11px] text-muted-foreground hover:text-ink">
            {t('common.retry')}
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
        {t('channels.check', { subject })}
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
            ? t('channels.notReady', { n: bad.length, name: bad[0].name })
            : tone === 'warn'
              ? t('channels.degraded', { ready, n: warned.length })
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
          {t('channels.recheck')}
        </button>
        <button
          aria-expanded={open}
          aria-label={t(open ? 'channels.collapseDetails' : 'channels.expandDetails')}
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
    <div className={cn(CARD_L2, 'overflow-hidden')}>
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
  const { t } = useI18n()
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
          <FixLink href={check.fix_url}>{t('channels.openPlatform')}</FixLink>
        )}
        {check.fix_note && (
          <div className="text-[11px] text-muted-foreground mt-1">{check.fix_note}</div>
        )}
        {/* 修复入口不在本页（如缺 Node.js）：送到唯一的那个入口，不在此复制一份安装按钮。
            标签取自 tab 名而非某一条检查的内容——这行是各条链路共用的，具体做什么由
            后端写在 detail / fix_note 里（同 fix_url 的「去开放平台配置」） */}
        {check.fix_nav && onNavigate && TAB_LABEL[check.fix_nav] && (
          <FixLink onClick={() => onNavigate(check.fix_nav)}>
            {t('channels.goTo', { tab: t(TAB_LABEL[check.fix_nav]) })}
          </FixLink>
        )}
      </div>
      {/* 就地修复按钮：一键安装（cli / 技能包），进行中由上方进度条替代 */}
      {fixAction && check.tone !== 'ok' && !progress && onFix && (
        <Button size="sm" className="mt-0.5 h-6 px-2.5 text-[11px]" onClick={() => onFix(fixAction)}>
          {t('channels.install')}
        </Button>
      )}
    </div>
  )
}

// 修复入口的金字淡底胶囊：外链（去开放平台）与面板跳转（去「环境」）共用一副皮
const fixLinkClass =
  'mt-1.5 inline-flex items-center gap-1 rounded-lg border border-primary/40 bg-primary/10 px-2.5 py-1 text-[11px] text-primary hover:bg-primary/20'
function FixLink({ href, onClick, children }: { href?: string; onClick?: () => void; children: ReactNode }) {
  return href ? (
    <a href={href} target="_blank" rel="noreferrer" className={fixLinkClass}>
      {children}
    </a>
  ) : (
    <button onClick={onClick} className={fixLinkClass}>
      {children}
    </button>
  )
}

import { createContext, useContext, useEffect } from 'react'
import type { ReactNode } from 'react'
import { WifiOff, RotateCw, Laptop, Cloud } from 'lucide-react'
import type { ConnState, ConnError, Machine } from '../types'
import { machineColor } from '@/lib/utils'
import { useI18n } from '../i18n'

// 机器表 + 各机器的实时连接态 + 重连入口。侧栏、设置各面板、项目/定时页都要按它决定
// 「这台机器现在能不能读写」，逐层透传要穿过五六个组件，故走 context（App 在根部一次性提供）。
type MachinesCtx = {
  machines: Machine[]
  conn: Record<string, ConnState>
  err: Record<string, ConnError> // 连不上的原因（'' = 没出过错）
  reconnect: (id: string) => void
}
const Ctx = createContext<MachinesCtx>({ machines: [], conn: {}, err: {}, reconnect: () => {} })
export const MachinesProvider = Ctx.Provider

export const useMachines = (): Machine[] => useContext(Ctx).machines
// 全量连接态：给侧栏这类在循环/辅助函数里按机器取状态、用不了按 id hook 的地方
export const useMachineConn = (): Record<string, ConnState> => useContext(Ctx).conn

// 某台机器现在处于哪一种状态：连上了 / 还在试 / 已经不试了。
// 关键是 closed 默认属于「还在试」——Gateway 撞断线后自行退避重连，每一轮都会
// connecting↔closed 来回跳；若把 connecting 当已连上就会把空面板闪出来，把 closed 当
// 已放弃则按钮会跟着跳。只有 failed（退避耗尽）与 1008（令牌无效，Gateway 不再重试）
// 才是真的停了，此时才给「重新连接」。
type ScopeState = 'connected' | 'retrying' | 'stopped'

export function useMachine(id: string): { scope: ScopeState; error: ConnError } {
  const { conn, err } = useContext(Ctx)
  const state = conn[id]
  const error = err[id] ?? ''
  const scope: ScopeState =
    state === 'open'
      ? 'connected'
      : state === 'failed' || (state === 'closed' && error === 'auth')
        ? 'stopped'
        : 'retrying'
  return { scope, error }
}

// 挂靠连接态的取数 effect：机器没连上时不发请求（Gateway 立即 reject 且无人重试），
// 连上/重连成功后自动重跑。fn 可返回清理函数，与 useEffect 同约定；deps 不含连接态。
export function useConnectedEffect(
  machine: string,
  fn: () => void | (() => void),
  deps: unknown[],
) {
  const { scope } = useMachine(machine)
  useEffect(() => {
    if (scope === 'connected') return fn()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, scope])
}

// 机器类型图标：本地=笔记本、远程=云端
const machineGlyph = (id: string) => (id === 'local' ? Laptop : Cloud)

// 一行/一处要标「哪台机器」时传的三元组（侧栏会话行与定时任务行、模型 chip 共用）
export type MachineMarker = { id: string; name: string; color: string }

// 机器标识的渲染本体：形状给「本地还是云端」、颜色给「哪一台」，一个元素说完两件事，
// 故取代了原先的纯色状态点。侧栏行 / 设置机器卡 / 模型 chip / 下面的 MachineIcon 共用
// 这一份，尺寸与 tooltip 由调用方给；不传 color 即继承外层文字色。
export function MachineMark({
  id,
  color,
  title,
  size = 12,
  className = '',
}: {
  id: string
  color?: string
  title?: string
  size?: number
  className?: string
}) {
  const Glyph = machineGlyph(id)
  return (
    <span title={title} className={`inline-flex shrink-0 ${className}`} style={color ? { color } : undefined}>
      <Glyph size={size} />
    </span>
  )
}

// 带连接态的机器标识（机器分组头 / 机器选择条）：还在重试时呼吸、真的停了才转灰
// ——状态按 useMachine 的 scope 判而非裸 conn：退避期 closed↔connecting 每轮来回跳，
// 裸判会让机器色一闪一灭，而颜色是「这行属于哪台机」的唯一载体，不能跟着眨。
export function MachineIcon({ id, size = 13 }: { id: string; size?: number }) {
  const { machines } = useContext(Ctx)
  const { scope } = useMachine(id)
  const stopped = scope === 'stopped'
  return (
    <MachineMark
      id={id}
      size={size}
      title={scope}
      color={stopped ? undefined : machineColor(id, machines)}
      className={`${stopped ? 'text-separator' : ''} ${scope === 'retrying' ? 'animate-pulse' : ''}`}
    />
  )
}

// 「重新连接」按钮：侧栏离线占位与作用域离线占位共用
export function ReconnectButton({ id, label, className = '' }: { id: string; label: string; className?: string }) {
  const { reconnect } = useContext(Ctx)
  return (
    <button
      onClick={() => reconnect(id)}
      className={`inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs text-ink transition hover:border-primary hover:text-primary ${className}`}
    >
      <RotateCw size={13} />
      {label}
    </button>
  )
}

// 「先选机器」选择条：多机时渲染一排机器 pill；单机自然退化为 null（无需调用方各自判断）。
function MachineTabs({
  value,
  onChange,
  className = 'flex flex-wrap gap-2 mb-4',
}: {
  value: string
  onChange: (id: string) => void
  className?: string
}) {
  const { conn } = useContext(Ctx)
  // 已关闭（不连接）的机器不出 pill
  const shown = useMachines().filter((m) => m.enabled !== false)
  if (shown.length <= 1) return null
  return (
    <div className={className}>
      {shown.map((m) => (
        <button
          key={m.id}
          onClick={() => onChange(m.id)}
          className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm border transition ${
            m.id === value
              ? 'border-primary/50 bg-primary/10 text-ink'
              : 'border-line text-muted-foreground hover:text-ink hover:border-separator'
          } ${conn[m.id] === 'open' ? '' : 'opacity-60'}`}
        >
          <MachineIcon id={m.id} size={14} />
          {m.name}
        </button>
      ))}
    </div>
  )
}

// 机器作用域：选择条 + 内容。选中的机器没连上时不渲染 children——配置读不到也写不进去，
// 与其显示一份空表单让人白填（保存请求发不出去且被静默吞掉），不如只留重连入口。
export function MachineScope({
  value,
  onChange,
  tabsClassName,
  children,
}: {
  value: string
  onChange: (id: string) => void
  tabsClassName?: string
  children: ReactNode
}) {
  const machines = useMachines()
  const { scope, error } = useMachine(value)
  // 选中的机器被删/被停用后本页会卡在一个再也切不回来的空选中态（pill 已被过滤掉，
  // 只剩一台时选择条整个不渲染，没有任何出口），故自动落回第一台可用机器
  const gone = !machines.some((m) => m.id === value && m.enabled !== false)
  const fallback = machines.find((m) => m.enabled !== false)?.id ?? 'local'
  useEffect(() => {
    if (gone) onChange(fallback)
  }, [gone, fallback, onChange])
  if (gone) return null // 下一帧就会带着 fallback 重来，这一帧不必渲染半个状态
  return (
    <>
      <MachineTabs value={value} onChange={onChange} className={tabsClassName} />
      {scope === 'connected' ? (
        children
      ) : (
        <MachineOffline id={value} retrying={scope === 'retrying'} auth={error === 'auth'} />
      )}
    </>
  )
}

// 未连上时的占位。容器与尺寸在两种状态下完全一致，只换图标/文案/按钮——
// 重连期间框不会消失也不会跳，视觉上就没有闪烁。
function MachineOffline({ id, retrying, auth }: { id: string; retrying: boolean; auth: boolean }) {
  const { t } = useI18n()
  return (
    <div className="flex min-h-[188px] flex-col items-center justify-center gap-2.5 rounded-xl border border-dashed border-separator px-4 py-10 text-center">
      {retrying ? (
        <span className="lumi-orb" style={{ width: 22, height: 22 }} />
      ) : (
        <WifiOff size={26} className="text-separator" />
      )}
      <div className="text-sm text-ink">{t(retrying ? 'machines.connecting' : 'machines.offline')}</div>
      <div className="max-w-[260px] text-[11.5px] text-muted-foreground">
        {t(retrying ? 'machines.connectingHint' : auth ? 'machines.authHint' : 'machines.offlineHint')}
      </div>
      {/* 还在自动重试时不给按钮：点了也只是同一件事，反而让人以为卡住了 */}
      {!retrying && <ReconnectButton id={id} label={t('machines.reconnect')} className="mt-1" />}
    </div>
  )
}

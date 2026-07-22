import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// 悬浮面板（左侧栏 / 右后台任务栏）与窗口边缘的间距。占位容器宽 = 面板宽 + 两倍间距，
// ResizeHandle 需回移一个间距贴到面板可见边缘。mac 红绿灯坐标以 electron/main.cjs 的
// trafficLightPosition 为唯一事实源（x 由本间距派生、y 含用户目感微调），改这里要同步那边。
export const FLOAT_GAP = 10

// L2 卡片配方（审批卡 / 后台任务卡共用）：与 SettingsKit 的 Card（L1，设置页容器）
// 是不同层级的材质——值只此一份，别在组件里手写字面量
export const CARD_L2 = 'border border-line/60 rounded-lg bg-surface/50'

// 文本截断与路径文件名提取（工具标题 / 计划对话框等共用）
export const clip = (s: string, n = 72) => (s.length > n ? s.slice(0, n) + '…' : s)
export const basename = (p: string) => p.split('/').filter(Boolean).pop() || p

// token 数格式化（≥1k 显示 x.xk）。与 TUI lumi/tui/widgets/agent_group.py::_format_tokens 同口径
export const fmtTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))

// 会话在 client 里的唯一身份 = 机器 id + thread_id。IM channel（飞书等）按 chat_id 派生
// 确定性 thread_id，同一个群在本地/远程两台 server 上会得到相同 thread_id；只用 thread_id
// 当 key 会让两台机器的同名会话在 client 里塌缩成一条（状态/连接/渲染全撞）。故一律用复合 key。
// 分隔符用 NUL：thread_id / backend id 都不含它，切分无歧义。发给后端的 wire 仍用裸 thread_id。
const KEY_SEP = '\u0000'
// backend id 归一：空/缺省即本地。会话、cron、bg 任务都用它认「哪台机器」，默认值收敛在这一处。
export const beOf = (x: { backend?: string | null }) => x.backend || 'local'
export const sessionKey = (backend: string, threadId: string) => `${backend}${KEY_SEP}${threadId}`
// 从复合 key 取回裸 thread_id（空串 / 无分隔符时原样返回）。
export const keyThread = (key: string) => key.slice(key.indexOf(KEY_SEP) + 1)
// 从复合 key 取回 backend id（无分隔符 / 空串时返回空串）。
export const keyBackend = (key: string) => {
  const i = key.indexOf(KEY_SEP)
  return i < 0 ? '' : key.slice(0, i)
}

// 机器识别色：本地走品牌金，远程从语法高亮调色板取（Sidebar / ModelPicker / 设置统一）
export const MACHINE_COLORS = ['#6fc7c0', '#e58a52', '#c79bd6', '#7fb3e0']
export function machineColor(id: string, machines: { id: string }[]): string {
  if (id === 'local') return 'var(--color-accent)'
  const idx = machines.filter((m) => m.id !== 'local').findIndex((m) => m.id === id)
  return MACHINE_COLORS[idx >= 0 ? idx % MACHINE_COLORS.length : 0]
}

// 机器显示名：由 backend id + 机器表现算，不在每条会话/任务上冗余存 machineName
export const machineName = (id: string, machines: { id: string; name: string }[]): string =>
  machines.find((m) => m.id === id)?.name ?? id

// 消息发送时刻（气泡头「李雷 · 14:02」）：当天只显时间，跨天补日期，跟随系统 locale。
// 每个用户气泡都要格式化一次（渠道会话重载还会整表重跑），Intl 构造较重，模块级缓存复用。
const _hmFmt = new Intl.DateTimeFormat([], { hour: '2-digit', minute: '2-digit' })
const _mdFmt = new Intl.DateTimeFormat([], { month: 'numeric', day: 'numeric' })
export function msgTime(ts: number): string {
  const d = new Date(ts)
  const hm = _hmFmt.format(d)
  if (d.toDateString() === new Date().toDateString()) return hm
  return `${_mdFmt.format(d)} ${hm}`
}

// 相对时间（"2 小时前"），跟随界面语言。Intl 构造较重，按 lang 缓存复用。
const _rtfCache = new Map<string, Intl.RelativeTimeFormat>()
export function timeAgo(seconds: number, lang: string): string {
  let rtf = _rtfCache.get(lang)
  if (!rtf) {
    rtf = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' })
    _rtfCache.set(lang, rtf)
  }
  const mins = Math.round((seconds * 1000 - Date.now()) / 60000)
  if (Math.abs(mins) < 60) return rtf.format(mins, 'minute')
  const hours = Math.round(mins / 60)
  if (Math.abs(hours) < 24) return rtf.format(hours, 'hour')
  return rtf.format(Math.round(hours / 24), 'day')
}

// gateway 对 error 帧 reject 的是 {message} 普通对象（Error 同样有 .message）——
// 各处 catch 回显错误文本统一走这里
export function errorMessage(e: unknown): string {
  const m = (e as { message?: unknown })?.message
  return typeof m === 'string' && m ? m : String(e)
}

import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// 文本截断与路径文件名提取（工具标题 / 计划对话框等共用）
export const clip = (s: string, n = 72) => (s.length > n ? s.slice(0, n) + '…' : s)
export const basename = (p: string) => p.split('/').filter(Boolean).pop() || p

// token 数格式化（≥1k 显示 x.xk）。与 TUI lumi/tui/widgets/agent_group.py::_format_tokens 同口径
export const fmtTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))

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

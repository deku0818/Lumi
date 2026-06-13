import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// 文本截断与路径文件名提取（工具标题 / 计划对话框等共用）
export const clip = (s: string, n = 72) => (s.length > n ? s.slice(0, n) + '…' : s)
export const basename = (p: string) => p.split('/').filter(Boolean).pop() || p

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

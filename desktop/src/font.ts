// 界面字体偏好：family（''=内置默认栈）+ size（正文字号 px）。
// localStorage 记忆，运行时覆写 body 继承的 --ui-font / --ui-font-size（见 index.css）。
import { useEffect, useState } from 'react'

const KEY = 'lumi-font'
// 默认正文字号，须与 index.css 的 var(--ui-font-size, 13px) 回退一致。
export const DEFAULT_SIZE = 13
export const MIN_SIZE = 11
export const MAX_SIZE = 20

export type FontPref = { family: string; size: number }

// 把字体族名转义成合法的 CSS 带引号字符串，防止名字里的引号/反斜杠破坏声明。
export function cssFamily(family: string): string {
  return `"${family.replace(/[\\"]/g, '\\$&')}"`
}

function initial(): FontPref {
  const raw = localStorage.getItem(KEY)
  if (!raw) return { family: '', size: DEFAULT_SIZE }
  try {
    const v = JSON.parse(raw)
    if (v && typeof v === 'object' && typeof v.family === 'string') {
      return { family: v.family, size: typeof v.size === 'number' ? v.size : DEFAULT_SIZE }
    }
  } catch {
    // 旧版本只存裸字体名字符串（JSON.parse 抛错）→ 迁移为 family
  }
  return { family: raw, size: DEFAULT_SIZE }
}

function apply({ family, size }: FontPref): void {
  // 值为 null → 移除覆写，回落到 index.css 的默认（--font-fallback / 13px）。
  const set = (prop: string, val: string | null) =>
    val ? document.documentElement.style.setProperty(prop, val) : document.documentElement.style.removeProperty(prop)
  // 回退栈引用 --font-fallback（默认栈唯一真相）：西文字体缺中文字形时回退。
  set('--ui-font', family ? `${cssFamily(family)}, var(--font-fallback)` : null)
  set('--ui-font-size', size !== DEFAULT_SIZE ? `${size}px` : null)
}

// 返回 [当前偏好, 设置偏好]
export function useUiFont(): [FontPref, (p: FontPref) => void] {
  const [pref, setPref] = useState<FontPref>(initial)
  useEffect(() => {
    apply(pref)
    localStorage.setItem(KEY, JSON.stringify(pref))
  }, [pref])
  return [pref, setPref]
}

// 枚举本机已装字体族名（去重 + 本地化排序）。
// 无 queryLocalFonts 能力（非 Electron / 未授权 / 用户拒绝）时返回 []。
export async function listLocalFonts(): Promise<string[]> {
  const query = (window as unknown as { queryLocalFonts?: () => Promise<{ family: string }[]> }).queryLocalFonts
  if (typeof query !== 'function') return []
  try {
    const fonts = await query()
    const families = new Set(fonts.map((f) => f.family))
    return [...families].sort((a, b) => a.localeCompare(b))
  } catch {
    return []
  }
}

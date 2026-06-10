// 主题偏好：system（跟随系统，随系统变化实时切换）/ light / dark。
// localStorage 记忆，root .light class 切换。effective 为实际生效的明/暗。
import { useEffect, useState } from 'react'

export type ThemePref = 'system' | 'light' | 'dark'
export type Theme = 'light' | 'dark'

const KEY = 'lumi-theme'

function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function initialPref(): ThemePref {
  const s = localStorage.getItem(KEY)
  return s === 'light' || s === 'dark' || s === 'system' ? s : 'system'
}

function effective(pref: ThemePref): Theme {
  return pref === 'system' ? systemTheme() : pref
}

function apply(theme: Theme): void {
  // .light 驱动我们的 token 覆盖；.dark 让 shadcn 组件的 dark: 变体在暗色下生效
  const root = document.documentElement
  root.classList.toggle('light', theme === 'light')
  root.classList.toggle('dark', theme === 'dark')
}

// 返回 [偏好, 设置偏好, 实际生效的明/暗]
export function useTheme(): [ThemePref, (p: ThemePref) => void, Theme] {
  const [pref, setPref] = useState<ThemePref>(initialPref)
  const [eff, setEff] = useState<Theme>(() => effective(initialPref()))

  useEffect(() => {
    const e = effective(pref)
    setEff(e)
    apply(e)
    localStorage.setItem(KEY, pref)
    if (pref !== 'system') return
    // 跟随系统：监听系统明暗切换实时更新
    const mq = window.matchMedia('(prefers-color-scheme: light)')
    const onChange = () => {
      const t = systemTheme()
      setEff(t)
      apply(t)
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [pref])

  return [pref, setPref, eff]
}

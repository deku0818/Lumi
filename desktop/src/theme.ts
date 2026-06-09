// 主题（lumi-dark / lumi-light）：初始跟随系统，localStorage 记忆，切换 root .light class。
import { useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'

const KEY = 'lumi-theme'

function initial(): Theme {
  const saved = localStorage.getItem(KEY)
  if (saved === 'dark' || saved === 'light') return saved
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function apply(theme: Theme): void {
  document.documentElement.classList.toggle('light', theme === 'light')
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initial)

  useEffect(() => {
    apply(theme)
    localStorage.setItem(KEY, theme)
  }, [theme])

  const toggle = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))
  return [theme, toggle]
}

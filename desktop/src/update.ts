import { useEffect, useState } from 'react'
import type { UpdateState } from './types'

export const RELEASES_URL = 'https://github.com/deku0818/Lumi/releases'

// 更新状态订阅：检查与下载全在主进程跑（见 electron/updater.cjs），这里只读 + 触发动作。
// 返回 null 表示宿主没有 update 能力（浏览器里跑 vite 调试），调用方据此整块隐藏 UI。
export function useUpdateState(): UpdateState | null {
  const [state, setState] = useState<UpdateState | null>(null)
  useEffect(() => {
    const api = window.lumi?.update
    if (!api) return
    api.state().then(setState)
    return api.onState(setState)
  }, [])
  return state
}

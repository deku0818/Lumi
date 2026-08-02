import { useEffect, useRef, useState } from 'react'
import type { Gateway } from '../gateway'
import type { EnvInstallTarget, EnvProgress, WireEventPayloads } from '../types'

// env 安装的订阅 + 乐观进度状态机，EnvPanel 与渠道体检共用一份：
// 进度按 target 累积、相关安装结束（env.state）清空并回调、触发侧乐观种子
// + started=false（全局互斥中）/请求失败时回滚。回调经 ref 取最新闭包，
// 订阅只依赖 gw——依赖状态会逐条进度事件拆建订阅，间隙丢事件。
export function useEnvInstall(
  gw: Gateway | undefined,
  opts: {
    // 本面板关心的安装目标；不传 = 全部
    targets?: EnvInstallTarget[]
    // 相关安装结束（env.state 到达）后调用，进度已清空
    onState?: (payload: WireEventPayloads['env.state']) => void
    // 安装请求本身被拒（RPC 错误，如旧版后端不认识该 target）：进度已回滚，
    // 调用方据此给出反馈——否则点击只闪一下「准备…」就归零，零线索
    onError?: (target: string, message: string) => void
  } = {},
) {
  const [progress, setProgress] = useState<Record<string, EnvProgress>>({})
  const optsRef = useRef(opts)
  optsRef.current = opts

  useEffect(() => {
    if (!gw) return
    const ours = (target: string) => {
      const targets = optsRef.current.targets
      // 'all'（一键装齐）按定义覆盖所有工具，恒视为相关——它的终态 env.state 带
      // target='all'，工具级订阅方（如 OfficePreview）无需各自记住这条约定
      return !targets || target === 'all' || (targets as string[]).includes(target)
    }
    return gw.onEvent((ev) => {
      if (ev.type === 'env.progress' && ours(ev.payload.target)) {
        const { target, phase, percent } = ev.payload
        setProgress((p) => ({ ...p, [target]: { phase, percent } }))
      } else if (ev.type === 'env.state' && ours(ev.payload.target)) {
        setProgress({})
        optsRef.current.onState?.(ev.payload)
      }
    })
  }, [gw])

  const install = (target: EnvInstallTarget, project = '') => {
    setProgress((p) => ({ ...p, [target]: { phase: '准备…', percent: -1 } }))
    gw
      ?.envInstall(target, project)
      .then((r) => {
        if (!r.started) setProgress({})
      })
      .catch((e) => {
        setProgress({})
        optsRef.current.onError?.(target, e instanceof Error ? e.message : String(e))
      })
  }

  // env_status 报告有安装进行中（本面板打开前触发的）时恢复进行中态
  const seed = (target: string) => {
    setProgress((p) => (p[target] ? p : { ...p, [target]: { phase: '安装中…', percent: -1 } }))
  }

  return { progress, install, seed }
}

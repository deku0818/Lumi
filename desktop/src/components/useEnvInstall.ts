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
    // 也关心「一键装齐」(target='all')：它逐工具装，含本面板的 target 时其终态
    // env.state 带 target='all'，需据此刷新（如 OfficePreview 等 officecli 装完重渲）。
    // 默认关（false）——否则任一处的 all 安装都会触发所有 scoped 订阅方的 onState，
    // 把无关面板的 onState 副作用（如渠道体检的 diagnose）误触发。
    watchAll?: boolean
    // 相关安装结束（env.state 到达）后调用，进度已清空
    onState?: (payload: WireEventPayloads['env.state']) => void
    // 安装请求被 RPC 拒（旧版后端不认识该 target 等）：message 是人话错误串，进度已回滚
    onError?: (target: string, message: string) => void
    // 因全局互斥被拒（started=false，已有安装在跑）：与 onError 分开，别让「busy」这类
    // 控制态挤进 onError 的自由文本 message——那会被直接渲染进错误横幅（EnvPanel）成
    // 未翻译的字面量。进度已回滚，调用方自行给「装完会继续」这类提示
    onBusy?: (target: string) => void
  } = {},
) {
  const [progress, setProgress] = useState<Record<string, EnvProgress>>({})
  const optsRef = useRef(opts)
  optsRef.current = opts

  useEffect(() => {
    if (!gw) return
    const ours = (target: string) => {
      const targets = optsRef.current.targets
      if (!targets) return true
      // 'all'（一键装齐）只对显式 opt-in（watchAll）的订阅方相关：它逐工具装，
      // 终态 env.state 带 target='all'；不 opt-in 的 scoped 面板不该被无关 all 安装
      // 触发 onState 副作用
      if (target === 'all') return !!optsRef.current.watchAll
      return (targets as string[]).includes(target)
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
        // started=false = 已有安装在跑（全局互斥）：清乐观进度并走 onBusy，否则按钮只闪
        // 一下「准备…」就归零、无任何线索（一键装齐进行中点单项安装即撞此路）
        if (!r.started) {
          setProgress({})
          optsRef.current.onBusy?.(target)
        }
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

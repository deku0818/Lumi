import { useEffect, useRef, useState } from 'react'
import type { Gateway } from '../gateway'
import type { FeishuConfig } from '../types'
import { errorMessage } from '@/lib/utils'

type AuthState =
  | { phase: 'idle' | 'preparing' | 'checking' | 'expired' }
  | { phase: 'waiting'; qrCode: string; url: string; expiresAt: number }
  | { phase: 'error'; message: string }

// 授权跟随表单生命周期；序号隔离取消 / 换机器人后晚到的响应，不能复活旧二维码。
export function useMinutesAuth(
  gw: Gateway | undefined,
  config: Partial<FeishuConfig>,
  onAuthorized: () => Promise<unknown> | undefined,
) {
  const [state, setState] = useState<AuthState>({ phase: 'idle' })
  const [seconds, setSeconds] = useState(0)
  const seq = useRef(0)
  const flow = useRef<{ gw: Gateway; id: string } | null>(null)

  const release = () => {
    ++seq.current
    const pending = flow.current
    flow.current = null
    if (pending) void pending.gw.cancelMinutesAuth(pending.id).catch(() => {})
  }
  const cancel = () => {
    release()
    setState({ phase: 'idle' })
  }

  useEffect(() => {
    setState({ phase: 'idle' })
    return release
  }, [gw, config.app_id, config.cli_profile]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (state.phase !== 'waiting') return
    const update = () => {
      const left = Math.max(0, Math.ceil((state.expiresAt - Date.now()) / 1000))
      setSeconds(left)
      if (!left) {
        release()
        setState({ phase: 'expired' })
      }
    }
    update()
    const timer = setInterval(update, 1000)
    return () => clearInterval(timer)
  }, [state]) // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    if (!gw) return
    release()
    const my = seq.current
    setState({ phase: 'preparing' })
    try {
      const result = await gw.startMinutesAuth(config)
      if (my !== seq.current) {
        if (result.status === 'waiting') void gw.cancelMinutesAuth(result.auth_id).catch(() => {})
        return
      }
      if (result.status === 'expired') return setState({ phase: 'expired' })
      flow.current = { gw, id: result.auth_id }
      setSeconds(Math.ceil(result.expires_in))
      setState({ phase: 'waiting', qrCode: result.qr_code, url: result.verification_url,
        expiresAt: Date.now() + result.expires_in * 1000 })
      const completed = await gw.finishMinutesAuth(result.auth_id)
      if (my !== seq.current) return
      flow.current = null
      if (completed.status === 'expired') return setState({ phase: 'expired' })
      if (completed.status === 'cancelled') return setState({ phase: 'idle' })
      setState({ phase: 'checking' })
      await onAuthorized()
      if (my === seq.current) setState({ phase: 'idle' })
    } catch (e) {
      if (my !== seq.current) return
      release()
      setState({ phase: 'error', message: errorMessage(e) })
    }
  }

  return { state, seconds, start, cancel }
}

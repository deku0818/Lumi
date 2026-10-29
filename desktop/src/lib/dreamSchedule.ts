import type { DailyDreamSchedule } from '../types'
import type { Gateway } from '../gateway'

// 预览随表单生命周期订阅；倒计时用服务器给出的时长，窗口唤醒时重新取数。
export function watchDreamSchedule(
  gw: Pick<Gateway, 'getDailyDreamSchedule'>,
  time: string,
  onChange: (schedule: DailyDreamSchedule | 'unavailable') => void,
): () => void {
  let sequence = 0
  let timer: ReturnType<typeof setTimeout>
  const refresh = async () => {
    const request = ++sequence
    clearTimeout(timer)
    const started = performance.now()
    try {
      const result = await gw.getDailyDreamSchedule(time)
      if (request !== sequence) return
      onChange(result)
      const delay = result.refresh_after_seconds * 1000 - (performance.now() - started)
      timer = setTimeout(refresh, Math.max(1000, delay))
    } catch {
      if (request === sequence) onChange('unavailable')
    }
  }
  const onVisible = () => { if (document.visibilityState === 'visible') void refresh() }
  void refresh()
  window.addEventListener('focus', refresh)
  document.addEventListener('visibilitychange', onVisible)
  return () => {
    ++sequence
    clearTimeout(timer)
    window.removeEventListener('focus', refresh)
    document.removeEventListener('visibilitychange', onVisible)
  }
}

function utcOffset(minutes: number): string {
  if (!minutes) return 'UTC'
  const abs = Math.abs(minutes)
  const tail = abs % 60 ? `:${String(abs % 60).padStart(2, '0')}` : ''
  return `UTC${minutes > 0 ? '+' : '−'}${Math.floor(abs / 60)}${tail}`
}

// 以服务器给出的执行时刻换算，不用客户端当前时间推算服务器的日期或偏移。
export function dreamTimePreview(nextRunAt: string, clientZone: string) {
  const instant = new Date(nextRunAt).getTime()
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: clientZone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(instant)
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find(p => p.type === type)!.value
  const clientDate = `${part('year')}-${part('month')}-${part('day')}`
  const clientTime = `${part('hour')}:${part('minute')}`
  // 把两端墙钟当作 UTC 数值，用于比较日期和计算各自相对同一时刻的偏移。
  const serverWall = Date.parse(`${nextRunAt.slice(0, 19)}Z`)
  const clientWall = Date.parse(`${clientDate}T${clientTime}:00Z`)
  const serverOffset = (serverWall - instant) / 60000
  const clientOffset = (clientWall - instant) / 60000
  return {
    serverTime: nextRunAt.slice(11, 16),
    serverOffset: utcOffset(serverOffset),
    clientTime,
    clientOffset: utcOffset(clientOffset),
    different: serverOffset !== clientOffset,
    dayOffset: Math.floor(clientWall / 86400000) - Math.floor(serverWall / 86400000),
  }
}

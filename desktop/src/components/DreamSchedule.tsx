import { useState, type JSX } from 'react'
import { ArrowRight, Info } from 'lucide-react'
import type { Gateway } from '../gateway'
import type { DailyDreamSchedule } from '../types'
import { useI18n, type Translate } from '../i18n'
import { dreamTimePreview, watchDreamSchedule } from '../lib/dreamSchedule'
import { useConnectedEffect, useMachine } from './MachineTabs'

function dayLabel(days: number, t: Translate): string {
  if (!days) return ''
  if (days === 1) return t('channels.dreamNextDay')
  if (days === -1) return t('channels.dreamPreviousDay')
  return t(days > 0 ? 'channels.dreamDaysAfter' : 'channels.dreamDaysBefore', { days: Math.abs(days) })
}

function ScheduleClock({ label, time, offset, zone, day }: {
  label: string; time: string; offset: string; zone: string; day?: string
}): JSX.Element {
  return <div className="flex-1 min-w-0">
    <div className="text-[10px] text-muted-foreground">{label}</div>
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <span className="text-[17px] font-medium tabular-nums">{time}</span>
      <span className="text-[10px] text-muted-foreground">{offset}</span>
      {day && <span className="rounded bg-primary/[0.08] px-1 text-[10px] text-primary">{day}</span>}
    </div>
    <div className="text-[10px] text-muted-foreground break-words">{zone}</div>
  </div>
}

export function DreamSchedule({ gw, machine, time }: { gw?: Gateway; machine: string; time: string }): JSX.Element | null {
  const { t } = useI18n()
  const { scope } = useMachine(machine)
  const [schedule, setSchedule] = useState<DailyDreamSchedule | 'loading' | 'unavailable'>('loading')
  const valid = /^\d{2}:\d{2}$/.test(time)
  useConnectedEffect(machine, () => {
    setSchedule('loading')
    if (!gw || !valid) return
    return watchDreamSchedule(gw, time, setSchedule)
  }, [gw, machine, time])

  if (!valid) return null
  if (typeof schedule === 'string' || !gw || scope !== 'connected') {
    const loading = schedule === 'loading' && gw && scope === 'connected'
    return <div className="col-span-2 text-[11px] text-muted-foreground" role="status">
      {t(loading ? 'channels.dreamTimezoneLoading' : 'channels.dreamTimezoneUnavailable')}
    </div>
  }
  const clientZone = Intl.DateTimeFormat().resolvedOptions().timeZone
  const preview = dreamTimePreview(schedule.next_run_at, clientZone)
  if (!preview.different) {
    return <div className="col-span-2 text-[11px] text-muted-foreground">
      {t('channels.dreamTimezoneSame')} · {schedule.timezone}（{preview.serverOffset}）
    </div>
  }
  return (
    <div className="col-span-2 rounded-lg border border-primary/20 bg-primary/[0.04] px-3 py-2.5" role="status">
      <div className="flex items-center gap-1.5 text-[11px] text-primary mb-1.5">
        <Info size={13} aria-hidden="true" />{t('channels.dreamTimezoneDifferent')}
      </div>
      <div className="flex items-center gap-3.5">
        <ScheduleClock label={t('channels.dreamServer')} time={preview.serverTime} offset={preview.serverOffset} zone={schedule.timezone} />
        <ArrowRight size={15} className="shrink-0 text-muted-foreground" aria-hidden="true" />
        <ScheduleClock label={t('channels.dreamClient')} time={preview.clientTime} offset={preview.clientOffset} zone={clientZone} day={dayLabel(preview.dayOffset, t)} />
      </div>
    </div>
  )
}

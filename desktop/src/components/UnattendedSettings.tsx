import { useState } from 'react'
import type { Gateway } from '../gateway'
import type { UnattendedToolMode } from '../types'
import { useI18n } from '../i18n'
import { MachineScope, useConnectedEffect } from './MachineTabs'
import { Loading, Row, Section, SegmentedControl } from './SettingsKit'
import { toast } from './Toast'
import { errorMessage } from '@/lib/utils'

export function UnattendedSettings({ gwFor }: { gwFor: (id: string) => Gateway | undefined }) {
  const { t } = useI18n()
  const [machine, setMachine] = useState('local')
  return (
    <Section title={t('settings.unattended')}>
      <MachineScope value={machine} onChange={setMachine}>
        <ModeSetting key={machine} machine={machine} gw={gwFor(machine)} />
      </MachineScope>
    </Section>
  )
}

function ModeSetting({ machine, gw }: { machine: string; gw?: Gateway }) {
  const { t } = useI18n()
  const [mode, setMode] = useState<UnattendedToolMode>()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  useConnectedEffect(machine, () => {
    let alive = true
    setMode(undefined)
    setError('')
    gw?.getRuntimeSettings().then((s) => {
      if (alive) setMode(s.unattended_tool_mode)
    }).catch((e) => {
      if (alive) setError(errorMessage(e))
    })
    return () => { alive = false }
  }, [gw, machine])

  const change = async (next: UnattendedToolMode) => {
    if (!gw || saving || next === mode) return
    setSaving(true)
    try {
      const s = await gw.setRuntimeSettings({ unattended_tool_mode: next })
      setMode(s.unattended_tool_mode)
    } catch (e) {
      toast.error(errorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  if (error) return <p className="text-sm text-[var(--color-error)]">{error}</p>
  if (!mode) return <Loading />
  return (
    <>
      <Row label={t('settings.unattendedMode')} hint={t('settings.unattendedHint')}>
        <fieldset disabled={saving} className="disabled:opacity-50">
          <SegmentedControl
            value={mode}
            onChange={change}
            options={[
              { val: 'auto', label: t('settings.unattendedAuto') },
              { val: 'privileged', label: t('settings.unattendedPrivileged') },
            ]}
          />
        </fieldset>
      </Row>
      {mode === 'privileged' && (
        <p className="mt-2 text-xs text-[var(--color-error)]">{t('settings.unattendedWarning')}</p>
      )}
    </>
  )
}

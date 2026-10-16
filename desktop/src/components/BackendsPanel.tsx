import { Laptop,Pencil,Plus,Server,Trash2 } from 'lucide-react'
import { useEffect,useState,type JSX } from 'react'
import { useI18n } from '../i18n.ts'
import type { BackendRemote,BackendsState,SshStatus } from '../types.ts'
import { ConfirmDialog } from './ConfirmDialog.tsx'
import { useMachine } from './MachineTabs.tsx'
import { Empty,EntityCard,Section,SectionGroup,StatusDot } from './SettingsKit.tsx'
import { SshHostForm,notifyBackendsChanged } from './SshHostForm.tsx'
import { Button } from './ui/button.tsx'
import { Switch } from './ui/switch.tsx'

export function BackendsPanel(): JSX.Element {
  const { t } = useI18n()
  const local = useMachine('local')
  const [state, setState] = useState<BackendsState>({ remotes: [] })
  const [editing, setEditing] = useState<BackendRemote | null | undefined>()
  const [deleting, setDeleting] = useState<BackendRemote | null>(null)
  const [statuses, setStatuses] = useState<Record<string, SshStatus>>({})
  function reload(): Promise<void> | undefined {
    return window.lumi.backends?.list().then(setState)
  }
  useEffect(() => {
    void reload()
    void window.lumi.ssh.statuses().then(items => setStatuses(Object.fromEntries(items.map(item => [item.id, item]))))
    return window.lumi.ssh.onStatus(status => setStatuses(current => ({ ...current, [status.id]: status })))
  }, [])
  async function toggle(machine: BackendRemote, enabled: boolean): Promise<void> {
    const next = await window.lumi.backends?.save({ id: machine.id, enabled })
    if (next) setState(next)
    notifyBackendsChanged()
  }
  return <SectionGroup>
    <Section title={t('settings.connections')} desc={t('backends.desc')}>
      <EntityCard icon={<Laptop size={16} />} title={t('backends.local')} subtitle={t('backends.localHint')} meta={<StatusDot tone={local.scope === 'connected' ? 'ok' : 'hollow'} pulse={local.scope === 'retrying'} />} />
    </Section>
    <Section title={t('backends.remotes')} action={<Button size="sm" variant="outline" onClick={() => setEditing(null)}><Plus />{t('ssh.add')}</Button>}>
      {!state.remotes.length && <Empty>{t('backends.empty')}</Empty>}
      <div className="space-y-2">{state.remotes.map(machine => {
        const status = statuses[machine.id]
        const active = machine.enabled !== false
        let tone: 'ok' | 'error' | 'hollow' = 'hollow'
        if (status?.phase === 'ready') tone = 'ok'
        else if (status?.phase === 'error') tone = 'error'
        return <EntityCard key={machine.id} dim={!active} icon={<Server size={16} />} title={machine.name || machine.host}
          meta={<StatusDot tone={tone} />}
          subtitle={active && status?.message ? status.message : machine.host}
          actions={<><Button variant="ghost" size="icon-sm" onClick={() => setEditing(machine)} aria-label={t('backends.edit')}><Pencil /></Button><Button variant="ghost" size="icon-sm" onClick={() => setDeleting(machine)} aria-label={t('common.delete')}><Trash2 /></Button></>}
          trailing={<Switch checked={active} onCheckedChange={enabled => void toggle(machine, enabled)} />} />
      })}</div>
    </Section>
    {editing !== undefined && <SshHostForm initial={editing ?? undefined} onClose={() => { setEditing(undefined); void reload() }} onConnected={() => { setEditing(undefined); void reload() }} />}
    {deleting && <ConfirmDialog title={t('backends.deleteTitle', { name: deleting.name || deleting.host })} message={t('backends.deleteMessage')} onCancel={() => setDeleting(null)} onConfirm={() => {
      void window.lumi.backends?.remove(deleting.id).then(next => { setState(next); notifyBackendsChanged() })
      setDeleting(null)
    }} />}
  </SectionGroup>
}

import { Laptop,Pencil,Plus,Server,Trash2 } from 'lucide-react'
import { useEffect,useRef,useState,type JSX } from 'react'
import { useI18n } from '../i18n.ts'
import type { BackendRemote,BackendsState } from '../types.ts'
import { ConfirmDialog } from './ConfirmDialog.tsx'
import { useMachine,useMachineSsh } from './MachineTabs.tsx'
import { sshBusy } from '../lib/sshStatus.ts'
import { SshConnectionBody } from './SshConnectionView.tsx'
import { Empty,EntityCard,Section,SectionGroup,StatusDot } from './SettingsKit.tsx'
import { SshHostForm,notifyBackendsChanged } from './SshHostForm.tsx'
import { Button } from './ui/button.tsx'
import { Switch } from './ui/switch.tsx'

export function BackendsPanel({ connectionTarget }: { connectionTarget?: { machine: string } }): JSX.Element {
  const { t } = useI18n()
  const local = useMachine('local')
  const [state, setState] = useState<BackendsState>({ remotes: [] })
  const [editing, setEditing] = useState<BackendRemote | null | undefined>()
  const [deleting, setDeleting] = useState<BackendRemote | null>(null)
  const statuses = useMachineSsh()
  const [expanded, setExpanded] = useState<string | undefined>(connectionTarget?.machine)
  const targetRef = useRef<HTMLDivElement>(null)
  function reload(): Promise<void> | undefined {
    return window.lumi.backends?.list().then(setState)
  }
  useEffect(() => {
    void reload()
  }, [])
  useEffect(() => { if (connectionTarget) setExpanded(connectionTarget.machine) }, [connectionTarget])
  useEffect(() => {
    if (!connectionTarget || expanded !== connectionTarget.machine) return
    targetRef.current?.scrollIntoView({ block: 'nearest' })
    targetRef.current?.focus({ preventScroll: true })
  }, [connectionTarget, state.remotes, expanded])
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
        const busy = active && sshBusy(status)
        const showProgress = expanded === machine.id && !!status
        let tone: 'ok' | 'error' | 'hollow' = 'hollow'
        if (status?.phase === 'ready') tone = 'ok'
        else if (status?.phase === 'error') tone = 'error'
        return <div key={machine.id} ref={connectionTarget?.machine === machine.id ? targetRef : undefined} tabIndex={-1} className="remote-connection">
          <EntityCard dim={!active} icon={<Server size={16} />} title={machine.name || machine.host}
            meta={<StatusDot tone={busy ? 'warn' : tone} pulse={busy} />}
            subtitle={active && status ? t('ssh.state.' + status.phase) : machine.host}
            badge={status && <Button variant="ghost" size="sm" className="text-[11px] text-muted-foreground" aria-expanded={showProgress} aria-controls={'connection-' + machine.id} onClick={() => setExpanded(showProgress ? undefined : machine.id)}>{t(showProgress ? 'ssh.hideProgress' : 'ssh.viewProgress')}</Button>}
            actions={<><Button disabled={busy} variant="ghost" size="icon-sm" onClick={() => setEditing(machine)} aria-label={t('backends.edit')}><Pencil /></Button><Button variant="ghost" size="icon-sm" onClick={() => setDeleting(machine)} aria-label={t('common.delete')}><Trash2 /></Button></>}
            trailing={<Switch checked={active} onCheckedChange={enabled => void toggle(machine, enabled)} />} />
          {showProgress && <div id={'connection-' + machine.id} className="remote-connection-progress">
            <SshConnectionBody machine={machine} status={status} question={null} error={status.phase === 'error' ? status.message : ''} showHost={false} />
            <div className="remote-connection-footer">
              <span>{t(busy ? 'ssh.backgroundProgress' : status.phase === 'ready' ? 'ssh.stateHint.ready' : 'ssh.stateHint.offline')}</span>
              {active && ['error', 'offline'].includes(status.phase) && <Button size="sm" variant="outline" onClick={() => {
                // The shared status subscription displays any connection error.
                void window.lumi.ssh.connect(machine.id).catch(() => {})
              }}>{t('ssh.retry')}</Button>}
            </div>
          </div>}
        </div>
      })}</div>
    </Section>
    {editing !== undefined && <SshHostForm initial={editing ?? undefined} onClose={() => { setEditing(undefined); void reload() }} onConnected={() => { setEditing(undefined); void reload() }} />}
    {deleting && <ConfirmDialog title={t('backends.deleteTitle', { name: deleting.name || deleting.host })} message={t('backends.deleteMessage')} onCancel={() => setDeleting(null)} onConfirm={() => {
      void window.lumi.backends?.remove(deleting.id).then(next => { setState(next); notifyBackendsChanged() })
      setDeleting(null)
    }} />}
  </SectionGroup>
}

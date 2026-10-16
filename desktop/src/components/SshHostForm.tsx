import { initialSshFields, type SshFormFields, type SshFormCredentials, type SshConnectionProcess } from '../lib/sshFormState.ts'
import { errorMessage } from '@/lib/utils'
import { Check,KeyRound,Plus,RefreshCw,Server,ShieldCheck } from 'lucide-react'
import { useEffect,useRef,useState,type JSX } from 'react'
import { useI18n } from '../i18n.ts'
import type { BackendRemote } from '../types.ts'
import { ScrollFade } from './ScrollFade.tsx'
import { Field,FormModal,TextInput } from './SettingsKit.tsx'
import { SshConnectionBody,SshQuestionAction } from './SshConnectionView.tsx'
import { useSshQuestion } from './SshPrompts.tsx'
import { Button } from './ui/button.tsx'
import { MenuSearch } from './ui/menu-search.tsx'

export function notifyBackendsChanged(reconnectId?: string): void {
  window.dispatchEvent(new CustomEvent('lumi:backends-changed', { detail: { reconnectId } }))
}

type SshHostFormProps = {
  initial?: BackendRemote
  onClose: () => void
  onConnected: (machine: BackendRemote) => void
  continueLabel?: string
}

export function SshHostForm({ initial, onClose, onConnected, continueLabel }: SshHostFormProps): JSX.Element {
  const { t } = useI18n()
  const [source, setSource] = useState(initial?.host ? 'manual' : 'config')
  const [hosts, setHosts] = useState<{ alias: string }[]>([])
  const [search, setSearch] = useState('')
  const [fields, setFields] = useState(() => initialSshFields(initial))
  const [credentials, setCredentials] = useState<SshFormCredentials>({ privateKey: '', passphrase: '', keepKey: false })
  const [resolved, setResolved] = useState('')
  const [process, setProcess] = useState<SshConnectionProcess>({ error: '', busyId: '', busy: false, progress: false, status: null })
  const { host, name, user, port, identityFile, dataDir, auth } = fields
  const { privateKey, passphrase, keepKey } = credentials
  const { error, busyId, busy, progress, status } = process

  function updateFields(patch: Partial<SshFormFields>): void {
    setFields(current => ({ ...current, ...patch }))
  }
  function updateCredentials(patch: Partial<SshFormCredentials>): void {
    setCredentials(current => ({ ...current, ...patch }))
  }
  function updateProcess(patch: Partial<SshConnectionProcess>): void {
    setProcess(current => ({ ...current, ...patch }))
  }

  const machineRef = useRef<BackendRemote | undefined>(initial?.id ? initial : undefined)
  const baseline = useRef(initial?.id ? initial : undefined)
  const attempt = useRef({ id: '', cancelled: false, committed: false, rolledBack: false })
  const { question, answer } = useSshQuestion(busyId)
  async function rollback(): Promise<void> {
    const current = attempt.current
    if (!current.id || current.committed || current.rolledBack) return
    current.rolledBack = true
    await window.lumi.ssh.disconnect(current.id)
    if (baseline.current) await window.lumi.backends?.save(baseline.current)
    else await window.lumi.backends?.remove(current.id)
    await window.lumi.ssh.cleanKeys(current.id)
    notifyBackendsChanged(baseline.current?.id)
  }
  useEffect(() => {
    attempt.current.cancelled = false
    return () => { attempt.current.cancelled = true; void rollback().catch(() => {}) }
  }, [])
  const loadHosts = () => window.lumi.ssh.hosts().then(setHosts).catch(e => updateProcess({ error: errorMessage(e) }))
  useEffect(() => { void loadHosts() }, [])
  // Subscribe before connect() so fast local IPC events cannot beat a React effect.
  useEffect(() => window.lumi.ssh.onStatus(state => { if (state.id === attempt.current.id && !attempt.current.cancelled) updateProcess({ status: state }) }), [])
  useEffect(() => {
    if (!host) { setResolved(''); return }
    let alive = true
    const timer = setTimeout(() => {
      void window.lumi.ssh.resolve({ host, user: user || undefined, port: port ? Number(port) : undefined })
        .then(value => { if (alive) setResolved(value.user + '@' + value.hostname + ':' + value.port + (value.jump ? ' · ' + value.jump : '')) })
        .catch(() => { if (alive) setResolved('') })
    }, 300)
    return () => { alive = false; clearTimeout(timer) }
  }, [host, user, port])
  function close(): void {
    updateCredentials({ privateKey: '' })
    updateCredentials({ passphrase: '' })
    attempt.current.cancelled = true
    if (question) answer(question.id, null)
    void rollback().catch(() => {})
    onClose()
  }
  async function connect(): Promise<void> {
    if (!window.lumi.backends || busy || !host.trim()) return
    updateProcess({ error: '' })
    if (port && (!Number.isInteger(Number(port)) || Number(port) < 1 || Number(port) > 65535)) {
      updateProcess({ error: t('ssh.invalidPort') })
      return
    }
    if (auth === 'file' && !identityFile.trim()) {
      updateProcess({ error: t('ssh.chooseKey') })
      return
    }
    if (auth === 'paste' && !privateKey.trim()) {
      updateProcess({ error: t('ssh.enterKey') })
      return
    }
    updateProcess({ busy: true })
    updateProcess({ progress: true })
    updateProcess({ status: { id: machineRef.current?.id || '', phase: 'connecting', message: '' } })
    attempt.current = { id: '', cancelled: false, committed: false, rolledBack: false }
    try {
      const connectionCredentials = { privateKey: auth === 'paste' ? privateKey : undefined, passphrase, keepKey }
      const state = await window.lumi.backends.save({
        id: machineRef.current?.id, name: name.trim() || host.trim(), host: host.trim(),
        user: user.trim(), port: port ? Number(port) : null, authMethod: auth,
        identityFile: auth === 'file' ? identityFile.trim() : '', dataDir: dataDir.trim(), enabled: true,
      }, connectionCredentials)
      const machine = machineRef.current ? state.remotes.find(item => item.id === machineRef.current!.id)! : state.remotes[state.remotes.length - 1]
      machineRef.current = machine
      attempt.current.id = machine.id
      updateProcess({ busyId: machine.id })
      if (attempt.current.cancelled) {
        await rollback()
        return
      }
      await window.lumi.ssh.connect(machine.id, keepKey ? { passphrase } : connectionCredentials)
      if (attempt.current.cancelled) {
        await rollback()
        return
      }
      // SSH is established. Keep it when the user closes the successful dialog.
      attempt.current.committed = true
      baseline.current = machine
      await window.lumi.ssh.cleanKeys(machine.id)
      updateCredentials({ privateKey: '' })
      updateCredentials({ passphrase: '' })
      if (keepKey && auth === 'paste') {
        updateFields({ auth: 'file' })
        updateFields({ identityFile: machine.identityFile || '' })
      }
      setProcess(current => ({ ...current, status: { ...current.status, id: machine.id, phase: 'ready', message: '' } }))
      notifyBackendsChanged(machine.id)
    } catch (error) {
      if (!attempt.current.cancelled) updateProcess({ error: errorMessage(error) })
    } finally {
      if (!attempt.current.cancelled) updateProcess({ busy: false })
    }
  }
  const ready = !busy && !error && status?.phase === 'ready'
  function edit(): void {
    updateProcess({ progress: false })
    updateProcess({ error: '' })
    updateProcess({ status: null })
  }
  const machine = machineRef.current || { name: name || host, host, user, port: port ? Number(port) : null, authMethod: auth, dataDir }

  function renderConnectionActions(): JSX.Element {
    if (question) {
      return <>
        <Button variant="outline" onClick={close}>{t('common.cancel')}</Button>
        <SshQuestionAction question={question} answer={answer} />
      </>
    }
    if (ready) {
      return <>
        <Button variant="outline" onClick={edit}>{t('ssh.editConnection')}</Button>
        <Button onClick={() => onConnected(machineRef.current!)}>{continueLabel || t('ssh.done')}</Button>
      </>
    }
    if (error) {
      return <>
        <Button variant="outline" onClick={edit}>{t('ssh.editConnection')}</Button>
        <Button onClick={() => void connect()}>{t('ssh.retry')}</Button>
      </>
    }
    return <>
      <Button variant="outline" onClick={close}>{t('ssh.cancelConnection')}</Button>
      <Button disabled>{t('common.connecting')}</Button>
    </>
  }

  const footer = progress ? <>
    <span className="ssh-footnote"><ShieldCheck size={12} />{t(question?.kind === 'prepare' ? 'ssh.confirmChanges' : 'ssh.viaSsh')}</span>
    {renderConnectionActions()}
  </> : <>
    <span className="ssh-footnote"><ShieldCheck size={12} />{t('ssh.checkOnConnect')}</span><Button variant="outline" onClick={close}>{t('common.cancel')}</Button><Button disabled={busy || !host.trim()} onClick={() => void connect()}>{t('ssh.connect')}</Button>
  </>
  return <FormModal title={t(ready ? 'ssh.connectedTitle' : 'ssh.connectionTitle')} description={t(progress ? 'ssh.progressHint' : 'ssh.configHint')} onClose={close}
    className="lumi-dialog ssh-dialog sm:max-w-[720px]" bodyClassName={progress ? 'ssh-body max-h-[65vh]' : 'ssh-body ssh-config-body max-h-[65vh]'} footer={footer}>
    {progress ? <SshConnectionBody machine={machine} status={status} question={question} error={error} answer={answer} /> : <div className="ssh-config-layout">
      <aside className="ssh-host-list">
        <div className="ssh-host-list-title"><span>{t('ssh.localHosts')}</span><Button variant="ghost" size="icon-xs" onClick={() => void loadHosts()} aria-label={t('ssh.refresh')}><RefreshCw size={13} /></Button></div>
        <MenuSearch value={search} onChange={e => setSearch(e.target.value)} placeholder={t('ssh.search')} aria-label={t('ssh.search')} />
        <ScrollFade className="ssh-host-scroll">{hosts.filter(item => item.alias.toLowerCase().includes(search.toLowerCase())).map(item => <button key={item.alias} className={'ssh-host-option ' + (host === item.alias && source === 'config' ? 'selected' : '')} onClick={() => { updateFields({ host: item.alias }); setSource('config') }}><Server size={14} /><span>{item.alias}</span>{host === item.alias && source === 'config' && <Check size={13} />}</button>)}</ScrollFade>
        {!hosts.length && <p className="text-[11px] text-muted-foreground px-2 py-3">{t('ssh.emptyConfig')}</p>}
        <button className={'ssh-host-option ssh-manual ' + (source === 'manual' ? 'selected' : '')} onClick={() => setSource('manual')}><Plus size={14} /><span>{t('ssh.manual')}</span></button>
      </aside>
      <div className="ssh-config-detail">
        <div className="ssh-selected-host"><span className="ssh-host-avatar"><Server size={18} /></span><div><strong>{host || t('ssh.selectMachine')}</strong><small>{resolved || t('ssh.localConfig')}</small></div></div>
        {source === 'manual' && <Field label={t('ssh.host')}><TextInput value={host} onChange={e => updateFields({ host: e.target.value })} placeholder="user@hostname" /></Field>}
        <Field label={t('backends.name')}><TextInput value={name} onChange={e => updateFields({ name: e.target.value })} placeholder={host} /></Field>
        <div className="ssh-auth-tabs" role="tablist" aria-label={t('ssh.auth')}>
          {(['config', 'file', 'paste', 'password'] as const).map(value => <button key={value} role="tab" aria-selected={auth === value} onClick={() => updateFields({ auth: value })}>{t('ssh.authTab.' + value)}</button>)}
        </div>
        {auth === 'config' && <div className="ssh-auth-hint"><KeyRound size={15} /><div><strong>{t('ssh.existingIdentity')}</strong><p>{t('ssh.existingIdentityHint')}</p></div></div>}
        {auth === 'password' && <div className="ssh-auth-hint"><ShieldCheck size={15} /><p>{t('ssh.passwordOnConnect')}</p></div>}
        {auth === 'file' && <><Field label={t('ssh.keyFile')}><div className="flex gap-2"><TextInput value={identityFile} onChange={e => updateFields({ identityFile: e.target.value })} /><Button variant="outline" onClick={() => void window.lumi.ssh.pickKey().then(file => { if (file) updateFields({ identityFile: file }) })}>{t('ssh.browse')}</Button></div></Field></>}
        {auth === 'paste' && <><Field label={t('ssh.privateKey')}><textarea value={privateKey} onChange={e => updateCredentials({ privateKey: e.target.value })} autoComplete="off" spellCheck={false} className="ssh-key-input" /></Field><label className="flex items-center gap-2 text-[11px] text-muted-foreground"><input type="checkbox" checked={keepKey} onChange={e => updateCredentials({ keepKey: e.target.checked })} />{t('ssh.keepKey')}</label></>}
        {(auth === 'file' || auth === 'paste') && <Field label={t('ssh.passphrase')}><TextInput type="password" value={passphrase} onChange={e => updateCredentials({ passphrase: e.target.value })} autoComplete="off" /></Field>}
        <details className="ssh-advanced"><summary>{t('ssh.loginSettings')}</summary><div className="flex gap-3"><Field label={t('ssh.user')}><TextInput value={user} onChange={e => updateFields({ user: e.target.value })} placeholder={t('ssh.inherit')} /></Field><Field label={t('ssh.port')}><TextInput type="number" min={1} max={65535} value={port} onChange={e => updateFields({ port: e.target.value })} placeholder={t('ssh.inherit')} /></Field></div><Field label={t('ssh.dataDir')} hint={t('ssh.dataDirHint')}><TextInput value={dataDir} onChange={e => updateFields({ dataDir: e.target.value })} placeholder="~/.lumi" /></Field></details>
        {error && <p role="alert" className="text-xs text-error">{error}</p>}
      </div>
    </div>}
  </FormModal>
}

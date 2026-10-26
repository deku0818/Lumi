import { basename,errorMessage } from '@/lib/utils'
import { defaultProjectTarget } from '@/lib/projects'
import { Check,ChevronDown,ChevronRight,Folder,Globe,Laptop,Plus,Server,Settings2,X } from 'lucide-react'
import { useEffect,useRef,useState,type JSX,type KeyboardEvent } from 'react'
import type { Gateway } from '../gateway.ts'
import { useI18n } from '../i18n.ts'
import type { BackendRemote,ProjectTarget } from '../types.ts'
import { DirBrowser } from './DirBrowser.tsx'
import { useMachineConn,useMachines } from './MachineTabs.tsx'
import { ScrollFade } from './ScrollFade.tsx'
import { Field,FormModal,NativeSelect,TextInput } from './SettingsKit.tsx'
import { SshHostForm } from './SshHostForm.tsx'
import { toast } from './Toast.tsx'
import { Button } from './ui/button.tsx'
import { DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuSeparator,DropdownMenuTrigger } from './ui/dropdown-menu.tsx'
import { MenuSearch } from './ui/menu-search.tsx'

type Connect = (backend: string) => Promise<Gateway>
type NewChatTargetProps = {
  value: ProjectTarget | null
  onChange: (target: ProjectTarget) => void
  ensureMachine: Connect
}

export function NewChatTarget({ value, onChange, ensureMachine }: NewChatTargetProps): JSX.Element {
  const { t } = useI18n()
  const machines = useMachines()
  const connections = useMachineConn()
  const [projects, setProjects] = useState<ProjectTarget[]>([])
  const [search, setSearch] = useState('')
  const [machineSearch, setMachineSearch] = useState('')
  const [editingHost, setEditingHost] = useState<BackendRemote | null | undefined>()
  const [creating, setCreating] = useState<'local' | 'remote' | null>(null)
  const [createBackend, setCreateBackend] = useState<string | undefined>()
  const [autoBrowse, setAutoBrowse] = useState(false)
  const [revision, setRevision] = useState(0)
  const choice = useRef(0)
  const projectSearch = useRef<HTMLInputElement>(null)
  const hostSearch = useRef<HTMLInputElement>(null)
  const backend = value?.backend || 'local'
  function machineName(id: string): string {
    if (id === 'local') return t('directory.computer')
    return machines.find(machine => machine.id === id)?.name || id
  }
  useEffect(() => {
    let alive = true
    void Promise.all(machines.filter(machine => machine.enabled !== false && connections[machine.id] === 'open').map(async machine => {
      try {
        const gateway = await ensureMachine(machine.id)
        const result = await gateway.listProjects()
        return { backend: machine.id, projects: result.projects.map(project => ({ backend: machine.id, path: project.path, name: project.name })) }
      } catch { return null }
    })).then(result => {
      if (!alive) return
      const available = result.filter((item): item is NonNullable<typeof item> => item !== null)
      const refreshed = new Set(available.map(item => item.backend))
      const configured = new Set(machines.filter(machine => machine.enabled !== false).map(machine => machine.id))
      setProjects(current => [...current.filter(project => configured.has(project.backend) && !refreshed.has(project.backend)), ...available.flatMap(item => item.projects)])
    })
    return () => { alive = false }
  }, [machines, connections, ensureMachine, revision])
  async function choose(target: ProjectTarget): Promise<void> {
    const seq = ++choice.current
    try {
      await ensureMachine(target.backend)
      if (seq === choice.current) onChange(target)
    } catch (error) {
      if (seq === choice.current) toast.error(errorMessage(error))
    }
  }
  async function chooseMachine(id: string): Promise<void> {
    const seq = ++choice.current
    try {
      const gateway = await ensureMachine(id)
      const result = await gateway.listProjects()
      if (seq !== choice.current) return
      onChange(defaultProjectTarget(id, result.projects, t('picker.choose')))
      setRevision(current => current + 1)
    } catch (e) { if (seq === choice.current) toast.error(errorMessage(e)) }
  }
  async function chooseWithoutProject(): Promise<void> {
    const seq = ++choice.current
    try {
      const gateway = await ensureMachine(backend)
      const home = await gateway.listDir('')
      if (seq === choice.current) onChange({ backend, path: home.path, name: t('ssh.noProject'), unscoped: true })
    } catch (e) { if (seq === choice.current) toast.error(errorMessage(e)) }
  }
  function create(mode: 'local' | 'remote'): void {
    setAutoBrowse(false)
    let targetBackend: string | undefined = backend
    if (mode === 'local') targetBackend = 'local'
    else if (backend === 'local') targetBackend = undefined
    setCreateBackend(targetBackend)
    setCreating(mode)
  }
  function searchKey(e: KeyboardEvent<HTMLInputElement>): void {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      e.currentTarget.closest('[role=menu]')?.querySelector<HTMLElement>('[role=menuitem]')?.focus()
    }
    if (e.key !== 'Escape') e.stopPropagation()
  }
  return <>
    <div className="new-chat-target">
      <DropdownMenu onOpenChange={open => { if (!open) setSearch('') }}>
        <DropdownMenuTrigger asChild><button className="new-chat-project" title={value?.unscoped ? t('ssh.noProjectHint') : value?.path}><Folder size={14} /><span>{value?.name || t('picker.choose')}</span><ChevronDown size={10} /></button></DropdownMenuTrigger>
        <DropdownMenuContent align="start" side="top" sideOffset={2} className="lumi-target-menu w-[338px] max-w-[calc(100vw-3rem)]" onFocusCapture={e => { if (e.target === e.currentTarget) projectSearch.current?.focus() }} onCloseAutoFocus={e => e.preventDefault()}>
          <MenuSearch ref={projectSearch} value={search} onChange={e => setSearch(e.target.value)} placeholder={t('ssh.projectSearch')} aria-label={t('ssh.projectSearch')} onKeyDown={searchKey} />
          <ScrollFade className="max-h-[146px]">
            {projects.filter(project => (project.name + ' ' + machineName(project.backend)).toLowerCase().includes(search.toLowerCase())).map(project =>
              <DropdownMenuItem className="lumi-target-item" key={project.backend + '\0' + project.path} onSelect={() => void choose(project)}><Folder size={14} /><span className="truncate">{project.name}</span>{project.backend !== 'local' && <span className="lumi-target-remote"><Globe size={11} /><span>{machineName(project.backend)}</span></span>}{value?.backend === project.backend && value.path === project.path && <Check size={13} className="ml-auto text-muted-foreground" />}</DropdownMenuItem>)}
          </ScrollFade>
          <DropdownMenuSeparator />
          <DropdownMenuItem className="lumi-target-item text-muted-foreground" onSelect={() => create('remote')}><Globe size={14} />{t('ssh.newRemoteProject')}</DropdownMenuItem>
          <DropdownMenuItem className="lumi-target-item text-muted-foreground" onSelect={() => create('local')}><Plus size={14} />{t('ssh.newLocalProject')}</DropdownMenuItem>
          <DropdownMenuItem className="lumi-target-item text-muted-foreground" title={t('ssh.noProjectHint')} onSelect={() => void chooseWithoutProject()}><X size={14} />{t('ssh.noProject')}</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu onOpenChange={open => { if (!open) setMachineSearch('') }}>
        <DropdownMenuTrigger asChild><button className="new-chat-machine">{backend === 'local' ? <Laptop size={14} /> : <Server size={14} />}<span>{machineName(backend)}</span></button></DropdownMenuTrigger>
        <DropdownMenuContent align="start" side="top" sideOffset={2} className="lumi-target-menu w-[338px] max-w-[calc(100vw-3rem)]" onFocusCapture={e => { if (e.target === e.currentTarget) hostSearch.current?.focus() }} onCloseAutoFocus={e => e.preventDefault()}>
          <MenuSearch ref={hostSearch} value={machineSearch} onChange={e => setMachineSearch(e.target.value)} placeholder={t('ssh.machineSearch')} aria-label={t('ssh.machineSearch')} onKeyDown={searchKey} />
          <ScrollFade className="max-h-[146px]">{machines.filter(machine => machine.enabled !== false && machineName(machine.id).toLowerCase().includes(machineSearch.toLowerCase())).map(machine => <DropdownMenuItem className="lumi-target-item" key={machine.id} onSelect={() => void chooseMachine(machine.id)}>{machine.id === 'local' ? <Laptop size={14} /> : <Server size={14} />}<span className="truncate">{machineName(machine.id)}</span>{backend === machine.id && <Check size={13} className="ml-auto text-muted-foreground" />}</DropdownMenuItem>)}</ScrollFade>
          <DropdownMenuSeparator /><DropdownMenuItem className="lumi-target-item text-muted-foreground" onSelect={() => setEditingHost(null)}><Plus size={14} />{t('ssh.add')}</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Button variant="ghost" size="icon-sm" className="ml-auto text-muted-foreground" aria-label={t('ssh.connectionSettings')} onClick={() => {
        if (backend === 'local') { setEditingHost(null); return }
        void window.lumi.backends?.list().then(state => setEditingHost(state.remotes.find(item => item.id === backend) || null))
      }}><Settings2 size={14} /></Button>
    </div>
    {editingHost !== undefined && <SshHostForm initial={editingHost ?? undefined} continueLabel={t('ssh.chooseProjectFolder')} onClose={() => setEditingHost(undefined)} onConnected={machine => {
      setEditingHost(undefined); setCreateBackend(machine.id); setAutoBrowse(true); setCreating('remote')
    }} />}
    {creating && <ProjectCreateDialog mode={creating} initialBackend={createBackend} autoBrowse={autoBrowse} ensureMachine={ensureMachine}
      onClose={() => setCreating(null)} onCreated={target => { setCreating(null); setRevision(current => current + 1); onChange(target) }} />}
  </>
}

type ProjectCreateDialogProps = {
  mode: 'local' | 'remote'
  initialBackend?: string
  autoBrowse?: boolean
  ensureMachine: Connect
  onCreated: (target: ProjectTarget) => void
  onClose: () => void
}

export function ProjectCreateDialog({ mode, initialBackend, autoBrowse = false, ensureMachine, onCreated, onClose }: ProjectCreateDialogProps): JSX.Element {
  const { t } = useI18n()
  const machines = useMachines()
  const [backend, setBackend] = useState(initialBackend || (mode === 'local' ? 'local' : machines.find(machine => machine.id !== 'local')?.id || ''))
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [gateway, setGateway] = useState<Gateway>()
  const [browse, setBrowse] = useState(false)
  const [sshDraft, setSshDraft] = useState<BackendRemote | null | undefined>()
  const [hosts, setHosts] = useState<{ alias: string }[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { void window.lumi.ssh.hosts().then(setHosts).catch(() => {}) }, [])

  async function openDirectory(machine: string, clearError = false): Promise<void> {
    setBusy(true)
    if (clearError) setError('')
    try {
      setGateway(await ensureMachine(machine))
      setBrowse(true)
    } catch (error) {
      setError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function chooseDirectory(): Promise<void> {
    if (!backend) {
      setSshDraft(null)
      return
    }
    if (backend.startsWith('ssh:')) {
      setSshDraft({ id: '', name: backend.slice(4), host: backend.slice(4) })
      return
    }
    await openDirectory(backend, true)
  }
  useEffect(() => { if (autoBrowse && initialBackend) void chooseDirectory() }, [])
  async function create(): Promise<void> {
    setBusy(true); setError('')
    try {
      const gw = await ensureMachine(backend)
      const projectName = name.trim() || basename(path)
      await gw.addProject(path, projectName)
      onCreated({ backend, path, name: projectName })
    } catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false) }
  }
  if (sshDraft !== undefined) return <SshHostForm initial={sshDraft ?? undefined} onClose={() => setSshDraft(undefined)} onConnected={machine => {
    setBackend(machine.id); setSshDraft(undefined)
    void openDirectory(machine.id)
  }} />
  if (browse) return <DirBrowser gw={gateway} machine={backend} onPick={value => { setPath(value); setBrowse(false) }} onCancel={() => setBrowse(false)} />
  return <FormModal title={t('ssh.createProject')} onClose={onClose} className="lumi-dialog sm:max-w-[470px]"
    footer={<><div className="flex-1" /><Button variant="outline" onClick={onClose}>{t('common.cancel')}</Button><Button disabled={!path || busy} onClick={() => void create()}>{t('ssh.createProject')}</Button></>}>
    <div className="space-y-4">
      <Field label={t('ssh.projectName')}><TextInput value={name} onChange={e => setName(e.target.value)} /></Field>
      <Field label={t('ssh.machine')}><NativeSelect value={backend} aria-label={t('ssh.machine')} onChange={e => { setBackend(e.target.value); setPath('') }} className="bg-canvas border-line text-xs">
        <option value="">{t('ssh.selectMachine')}</option>
        {machines.map(machine => <option key={machine.id} value={machine.id}>{machine.name}</option>)}
        {!!hosts.length && <optgroup label="SSH config">{hosts.map(host => <option key={host.alias} value={'ssh:' + host.alias}>{host.alias}</option>)}</optgroup>}
      </NativeSelect></Field>
      <Field label={t('directory.projectFolder')}><button className="project-folder-card" disabled={busy} onClick={() => void chooseDirectory()}><Folder size={25} /><span><strong>{path ? basename(path) : t('ssh.chooseFolder')}</strong><small>{path || t('ssh.chooseFolderHint')}</small></span>{busy ? <span className="lumi-orb lumi-orb-sm" /> : <ChevronRight size={14} />}</button></Field>
      <Button variant="ghost" onClick={() => setSshDraft(null)}><Plus />{t('ssh.add')}</Button>
      {error && <p role="alert" className="text-xs text-error">{error}</p>}
    </div>
  </FormModal>
}

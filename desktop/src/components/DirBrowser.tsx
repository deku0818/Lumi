import { directoryCrumbs } from '@/lib/directory'
import { basename,errorMessage } from '@/lib/utils'
import { ArrowRight,ArrowUp,ChevronRight,Folder,FolderPlus,Home,Laptop,SquarePen,Server,X } from 'lucide-react'
import { useCallback,useEffect,useRef,useState,type JSX } from 'react'
import type { Gateway } from '../gateway.ts'
import { useI18n } from '../i18n.ts'
import { useMachines } from './MachineTabs.tsx'
import { ScrollFade } from './ScrollFade.tsx'
import { Button } from './ui/button.tsx'
import { Dialog,DialogContent,DialogDescription,DialogTitle } from './ui/dialog.tsx'

type DirBrowserProps = {
  gw?: Gateway
  machine: string
  onPick: (path: string) => void
  onCancel: () => void
}

/** All project entry points browse the filesystem belonging to this Gateway. */
export function DirBrowser({ gw, machine, onPick, onCancel }: DirBrowserProps): JSX.Element {
  const { t } = useI18n()
  const machines = useMachines()
  const name = machine === 'local' ? t('directory.computer') : machines.find(item => item.id === machine)?.name || machine
  const MachineIcon = machine === 'local' ? Laptop : Server
  const [cwd, setCwd] = useState('')
  const [address, setAddress] = useState('')
  const [parent, setParent] = useState<string | null>(null)
  const [dirs, setDirs] = useState<string[]>([])
  const [selectable, setSelectable] = useState(false)
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [making, setMaking] = useState(false)
  const [newName, setNewName] = useState('')
  const [error, setError] = useState('')
  const [mkError, setMkError] = useState('')
  const request = useRef(0)
  const home = useRef('')
  const crumbsRef = useRef<HTMLDivElement>(null)
  const load = useCallback(async (path: string) => {
    if (!gw) return
    const seq = ++request.current
    setLoading(true)
    setError('')
    try {
      const result = await gw.listDir(path)
      if (seq !== request.current) return
      if (!home.current) home.current = result.path
      setCwd(result.path)
      setAddress(result.path)
      setParent(result.parent)
      setDirs(result.dirs)
      setSelectable(result.selectable ?? true)
      setEditing(false)
      setCreating(false)
      setMkError('')
    } catch (error) {
      if (seq === request.current) setError(errorMessage(error))
    } finally {
      if (seq === request.current) setLoading(false)
    }
  }, [gw])
  useEffect(() => {
    home.current = ''
    void load('')
    return () => { request.current++ }
  }, [load])
  useEffect(() => {
    const el = crumbsRef.current
    if (el) el.scrollLeft = el.scrollWidth
  }, [cwd, editing])
  function join(name: string): string {
    if (!cwd) return name
    if (/[\\/]$/.test(cwd)) return cwd + name
    const separator = cwd.includes('\\') ? '\\' : '/'
    return cwd + separator + name
  }

  async function mkdir(): Promise<void> {
    const name = newName.trim()
    if (!gw || !selectable || !name || making) return
    if (name === '.' || name === '..' || /[\\/]/.test(name)) { setMkError(t('ssh.invalidFolderName')); return }
    const seq = request.current
    setMaking(true); setMkError('')
    try {
      const result = await gw.makeDir(join(name))
      if (seq !== request.current) return
      if (!result.ok) { setMkError(result.error || t('projects.mkdirFailed')); return }
      setNewName(''); await load(cwd)
    } catch (e) { if (seq === request.current) setMkError(errorMessage(e)) }
    finally { setMaking(false) }
  }
  const busy = loading || making
  const crumbs = directoryCrumbs(cwd)
  function emptyLabel(): string {
    if (loading) return t('common.loading')
    if (error) return t('directory.unavailable')
    return t('projects.noSubdir')
  }
  return <Dialog open onOpenChange={open => !open && onCancel()}>
    <DialogContent className="lumi-dialog directory-dialog p-0 gap-0 overflow-hidden sm:max-w-[560px]" showCloseButton={false}
      onEscapeKeyDown={e => {
        if (!creating && !editing) return
        e.preventDefault()
        if (creating) setCreating(false)
        else { setEditing(false); setAddress(cwd); setError('') }
      }}>
      <div className="directory-heading">
        <DialogTitle className="text-[16px] text-ink">{t('ssh.chooseFolder')}</DialogTitle>
        <span className="lumi-machine-pill" title={name}><MachineIcon size={12} /><span>{name}</span></span>
        <Button variant="ghost" size="icon-sm" className="ml-auto text-muted-foreground" onClick={onCancel} aria-label={t('common.close')}><X /></Button>
        <DialogDescription className="sr-only">{t('directory.description', { machine: name })}</DialogDescription>
      </div>
      <div className="directory-navigation">
        <Button variant="ghost" size="icon" disabled={parent === null || busy} onClick={() => parent !== null && void load(parent)} title={t('projects.parentDir')} aria-label={t('projects.parentDir')}><ArrowUp /></Button>
        <Button variant="ghost" size="icon" disabled={!home.current || busy} onClick={() => void load(home.current)} title={t('directory.home')} aria-label={t('directory.home')}><Home /></Button>
        <div className="directory-address" title={cwd}>
          {editing ? <><input autoFocus value={address} aria-label={t('ssh.directoryPath')} onChange={e => setAddress(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) void load(address) }} />
            <Button variant="ghost" size="icon-xs" disabled={busy} onClick={() => void load(address)} aria-label={t('ssh.go')}><ArrowRight /></Button></> : <>
            <div ref={crumbsRef} className="directory-crumbs">
              {!selectable && !cwd ? <span>{t('directory.computer')}</span> : crumbs.map((crumb, i) => <span key={crumb.path}>
                {i > 0 && <ChevronRight size={11} />}<button disabled={busy} title={crumb.path} aria-label={crumb.path} onClick={() => void load(crumb.path)}>{i === 0 ? <><MachineIcon size={12} /><span>{crumb.label === '/' ? '' : crumb.label}</span></> : crumb.label}</button>
              </span>)}
            </div>
            <Button variant="ghost" size="icon-xs" disabled={busy} onClick={() => { setAddress(cwd); setEditing(true) }} title={t('directory.editPath')} aria-label={t('directory.editPath')}><SquarePen /></Button>
          </>}
        </div>
        <Button variant="ghost" className="directory-new" disabled={!selectable || busy} onClick={() => { setCreating(!creating); setMkError('') }} title={t('projects.newFolder')} aria-label={t('projects.newFolder')}><FolderPlus /><span>{t('projects.newFolder')}</span></Button>
      </div>
      {error && <p role="alert" className="directory-error">{error}</p>}
      <div className="directory-list-shell">
        <ScrollFade className="directory-list">
          {dirs.length ? dirs.map(dir => <button key={dir} disabled={busy} className="directory-row" onClick={() => void load(join(dir))}>
            <Folder size={15} /><span>{dir}</span><ChevronRight size={12} />
          </button>) : <div className="directory-empty"><Folder size={27} /><span>{emptyLabel()}</span></div>}
        </ScrollFade>
      </div>
      {creating && <div className="directory-mkdir">
        <input autoFocus value={newName} disabled={making} onChange={e => { setNewName(e.target.value); setMkError('') }} aria-label={t('projects.folderName')} placeholder={t('projects.folderName')}
          onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) void mkdir() }} />
        <Button variant="outline" disabled={making || !newName.trim()} onClick={() => void mkdir()}>{t('projects.create')}</Button>
        <Button variant="ghost" size="icon" onClick={() => setCreating(false)} aria-label={t('common.cancel')}><X /></Button>
      </div>}
      {mkError && <p role="alert" className="directory-error">{mkError}</p>}
      <div className="directory-meta"><span>{busy && <><span className="lumi-orb lumi-orb-sm" />{t('common.loading')}</>}</span><span>{t('directory.count', { count: dirs.length })}</span></div>
      <div className="directory-footer">
        <div className="directory-selection" title={cwd}><Folder size={16} /><div><strong>{basename(cwd) || (selectable ? cwd : t('directory.computer'))}</strong><small>{t('directory.current')}</small></div></div>
        <Button variant="outline" onClick={onCancel}>{t('common.cancel')}</Button>
        <Button disabled={!cwd || !selectable || busy || editing} onClick={() => onPick(cwd)}>{t('directory.choose')}</Button>
      </div>
    </DialogContent>
  </Dialog>
}

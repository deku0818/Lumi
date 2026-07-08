import { useCallback, useEffect, useState } from 'react'
import { Folder, FolderPlus, CornerLeftUp } from 'lucide-react'
import type { Gateway } from '../gateway'
import { useI18n } from '../i18n'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

// 通用远程目录浏览器：经传入的连接（某台机器）浏览它的文件系统、可新建文件夹，
// 选定目录回调 onPick(path)。新建项目 / 添加可访问目录共用——确保选的是「目标机器」的路径。
export function DirBrowser({
  gw,
  title,
  onPick,
  onCancel,
}: {
  gw?: Gateway
  title: string
  onPick: (path: string) => void
  onCancel: () => void
}) {
  const { t } = useI18n()
  const [cwd, setCwd] = useState('')
  const [parent, setParent] = useState<string | null>(null)
  const [dirs, setDirs] = useState<string[]>([])
  const [selectable, setSelectable] = useState(true)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [mkErr, setMkErr] = useState('')

  const load = useCallback(
    async (path: string) => {
      if (!gw) return
      try {
        const r = await gw.listDir(path)
        setCwd(r.path)
        setParent(r.parent)
        setDirs(r.dirs)
        setSelectable(r.selectable ?? true)
      } catch {
        /* 连接波动：保持现状 */
      }
    },
    [gw],
  )

  useEffect(() => {
    void load('') // 初始 = home
  }, [load])

  const join = (d: string) => {
    if (!cwd) return d
    return cwd.endsWith('/') || cwd.endsWith('\\') ? cwd + d : cwd + '/' + d
  }

  const doMkdir = async () => {
    const name = newName.trim()
    if (!name || !gw || !selectable) return
    setMkErr('')
    const r = await gw.makeDir(join(name)).catch(() => ({ ok: false, error: '请求失败' }))
    if (!r.ok) {
      setMkErr(r.error || '新建失败') // 权限不足/同名文件等：保留输入，给出原因
      return
    }
    setCreating(false)
    setNewName('')
    void load(cwd)
  }

  const input =
    'flex-1 rounded-lg border border-line bg-canvas px-3 py-1.5 text-sm outline-none focus:border-primary/40'

  return (
    <Dialog open onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="sr-only">{title}</DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2 text-xs">
          <button
            disabled={!parent}
            onClick={() => parent && void load(parent)}
            className="shrink-0 size-7 grid place-items-center rounded-md text-muted-foreground hover:text-ink hover:bg-line/30 disabled:opacity-30 transition"
            title={t('projects.parentDir')}
          >
            <CornerLeftUp size={15} />
          </button>
          <span className="truncate font-mono text-muted-foreground" title={cwd || t('projects.computer')}>
            {cwd || t('projects.computer')}
          </span>
        </div>

        <div className="h-56 overflow-auto rounded-xl border border-line bg-canvas">
          {dirs.length === 0 ? (
            <div className="p-4 text-xs text-muted-foreground/70">{t('projects.noSubdir')}</div>
          ) : (
            dirs.map((d) => (
              <button
                key={d}
                onClick={() => void load(join(d))}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-left hover:bg-surface transition"
              >
                <Folder size={14} className="shrink-0 text-muted-foreground" />
                <span className="truncate">{d}</span>
              </button>
            ))
          )}
        </div>

        {selectable && creating ? (
          <div>
            <div className="flex items-center gap-2">
              <input
                autoFocus
                value={newName}
                onChange={(e) => {
                  setNewName(e.target.value)
                  setMkErr('')
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void doMkdir()
                  else if (e.key === 'Escape') setCreating(false)
                }}
                placeholder={t('projects.folderName')}
                className={input}
              />
              <Button size="sm" onClick={() => void doMkdir()}>
                {t('projects.create')}
              </Button>
            </div>
            {mkErr && <div className="mt-1 text-xs text-error">{mkErr}</div>}
          </div>
        ) : selectable ? (
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-ink transition w-fit"
          >
            <FolderPlus size={14} />
            {t('projects.newFolder')}
          </button>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          <Button disabled={!cwd || !selectable} onClick={() => onPick(cwd)}>
            {t('projects.useThisDir')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

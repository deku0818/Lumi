import { useState, type ReactNode } from 'react'
import { AlertTriangle, Check, ChevronDown, Folder, FolderPlus } from 'lucide-react'
import type { Project } from '../types'
import type { Gateway } from '../gateway'
import { useI18n } from '../i18n'
import { ConfirmDialog } from './ConfirmDialog'
import { DirBrowser } from './DirBrowser'
import { useConnectedEffect } from './MachineTabs'
import { basename, cn, errorMessage } from '@/lib/utils'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

// 项目选择器：从该机器已登记的项目里挑一个（MCP 项目作用范围 / 飞书绑定项目共用）。
// 列表挂在机器连接态上（瞬断连回自动重拉）；「新建项目」经 DirBrowser 登记后直接选中，
// 登记失败在下方说出来——否则弹窗一关什么都没变，用户以为已经选上了。
// taken：被别人占用的项目（路径 → 占用者），置灰并注明；confirmSwitch：已有值时换选先弹确认
// （文案由调用方给，如飞书的「会重置会话」）；required：空值以错误色催选；
// autoDefault：列表到达且尚无选中时自动选默认（无默认则首个）项目。
export function ProjectPicker({
  gw,
  machine,
  value,
  onChange,
  taken,
  confirmSwitch,
  required,
  autoDefault,
}: {
  gw?: Gateway
  machine: string
  value: string
  onChange: (path: string) => void
  taken?: Map<string, string>
  confirmSwitch?: { title: ReactNode; message: (from: string, to: string) => ReactNode; confirmLabel: string }
  required?: boolean
  autoDefault?: boolean
}) {
  const { t } = useI18n()
  const [projects, setProjects] = useState<Project[]>([])
  const [creating, setCreating] = useState(false)
  const [pending, setPending] = useState<string | null>(null) // 待确认切换的目标路径
  const [addErr, setAddErr] = useState('')

  useConnectedEffect(
    machine,
    () => {
      let alive = true
      gw
        ?.listProjects()
        .then((r) => {
          if (!alive) return
          const ps = r.projects ?? []
          setProjects(ps)
          if (autoDefault && !value && ps.length) onChange((ps.find((p) => p.default) ?? ps[0]).path)
        })
        .catch(() => alive && setProjects([]))
      return () => {
        alive = false
      }
    },
    [gw],
  )

  const choose = (path: string) => {
    if (path === value) return
    if (confirmSwitch && value) setPending(path)
    else onChange(path)
  }

  const onCreated = (path: string) => {
    setCreating(false)
    setAddErr('')
    gw
      ?.addProject(path)
      .then((r) => {
        setProjects(r.projects ?? [])
        choose(path)
      })
      .catch((e) => setAddErr(t('picker.addFailed', { error: errorMessage(e) })))
  }

  const current = projects.find((p) => p.path === value)
  const label = current ? current.name : value ? basename(value) : t('picker.choose')
  const missing = required && !value

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            title={value}
            className={cn(
              'group flex shrink-0 items-center gap-2 rounded-lg border bg-surface px-2.5 py-1.5 text-left text-xs outline-none transition data-[state=open]:border-primary',
              missing ? 'border-error/60 text-error' : 'border-line text-ink',
            )}
          >
            <Folder size={14} className={cn('shrink-0', missing ? 'text-error' : 'text-primary')} />
            <span className="truncate max-w-[180px]">{label}</span>
            <ChevronDown
              size={13}
              className="shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
            />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {projects.map((p) => {
            const holder = taken?.get(p.path)
            return (
              <DropdownMenuItem key={p.path} disabled={!!holder} onClick={() => !holder && choose(p.path)}>
                <Check className={`text-primary ${p.path === value ? 'opacity-100' : 'opacity-0'}`} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-ink">{p.name}</div>
                  <div className="truncate font-mono text-[10px] text-muted-foreground">{p.path}</div>
                </div>
                {holder && (
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {t('picker.takenBy', { name: holder })}
                  </span>
                )}
              </DropdownMenuItem>
            )
          })}
          {projects.length > 0 && <DropdownMenuSeparator />}
          <DropdownMenuItem onClick={() => setCreating(true)} className="text-muted-foreground">
            <FolderPlus />
            {t('projects.new')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {addErr && <div className="text-[11px] text-error">{addErr}</div>}

      {creating && (
        <DirBrowser gw={gw} title={t('projects.new')} onPick={onCreated} onCancel={() => setCreating(false)} />
      )}

      {pending !== null && confirmSwitch && (
        <ConfirmDialog
          variant="default"
          icon={<AlertTriangle size={17} className="text-primary" />}
          title={confirmSwitch.title}
          message={
            <>
              {confirmSwitch.message(value, pending)}
              <span className="mt-3 flex items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-2 font-mono text-[11px]">
                <span className="truncate text-muted-foreground line-through">{value}</span>
                <span className="shrink-0 text-primary">→</span>
                <span className="truncate text-ink">{pending}</span>
              </span>
            </>
          }
          confirmLabel={confirmSwitch.confirmLabel}
          onConfirm={() => {
            onChange(pending)
            setPending(null)
          }}
          onCancel={() => setPending(null)}
        />
      )}
    </>
  )
}

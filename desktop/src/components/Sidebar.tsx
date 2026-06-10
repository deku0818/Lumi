import { useEffect, useRef, useState } from 'react'
import {
  MoreVertical,
  Pin,
  PinOff,
  Pencil,
  Trash2,
  Settings,
  Globe,
  Check,
  ChevronsUpDown,
} from 'lucide-react'
import type { ConnState } from '../gateway'
import type { SessionMeta } from '../types'
import { useI18n, LANGS } from '../i18n'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
} from '@/components/ui/dropdown-menu'
import { Button } from '@/components/ui/button'

const CONN_DOT: Record<ConnState, string> = {
  connecting: 'bg-primary',
  open: 'bg-success',
  closed: 'bg-error',
}

export function Sidebar({
  sessions,
  currentThread,
  conn,
  model,
  activity,
  disabled,
  onSelect,
  onNew,
  onOpenSettings,
  onPin,
  onRename,
  onDelete,
}: {
  sessions: SessionMeta[]
  currentThread: string
  conn: ConnState
  model: string
  activity: Record<string, 'running' | 'attention'>
  disabled: boolean
  onSelect: (threadId: string) => void
  onNew: () => void
  onOpenSettings: () => void
  onPin: (threadId: string, pinned: boolean) => void
  onRename: (threadId: string, title: string) => void
  onDelete: (session: SessionMeta) => void
}) {
  const { t } = useI18n()
  return (
    <aside className="w-64 shrink-0 bg-canvas border-r border-line/20 flex flex-col">
      <div className="h-9 app-drag shrink-0" />
      <div className="px-3 pb-3">
        <Button
          variant="ghost"
          onClick={onNew}
          disabled={disabled}
          className="no-drag w-full justify-start gap-2 h-auto px-3 py-2 rounded-xl"
        >
          <span className="text-primary text-base leading-none">＋</span>
          {t('sidebar.newChat')}
        </Button>
      </div>

      <div className="flex-1 overflow-auto px-2">
        {sessions.length > 0 && (
          <div className="text-xs text-muted px-3 pt-2 pb-1.5">{t('sidebar.recent')}</div>
        )}
        {sessions.map((s) => (
          <SessionRow
            key={s.thread_id}
            session={s}
            active={s.thread_id === currentThread}
            state={activity[s.thread_id]}
            disabled={disabled}
            onSelect={onSelect}
            onPin={onPin}
            onRename={onRename}
            onDelete={onDelete}
          />
        ))}
      </div>

      <div className="p-2 border-t border-line/20">
        <AccountMenu conn={conn} model={model} onOpenSettings={onOpenSettings} />
      </div>
    </aside>
  )
}

// 左下角账户入口：向上弹出菜单（设置 / 语言子菜单悬停右侧飞出）。
function AccountMenu({
  conn,
  model,
  onOpenSettings,
}: {
  conn: ConnState
  model: string
  onOpenSettings: () => void
}) {
  const { t, lang, setLang } = useI18n()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-surface transition text-left outline-none">
          <span className="relative shrink-0 size-6 grid place-items-center rounded-full bg-primary/15 text-primary text-sm">
            ✦
            <span
              className={`absolute -right-0.5 -bottom-0.5 size-2 rounded-full ring-2 ring-canvas ${CONN_DOT[conn]}`}
            />
          </span>
          <span className="flex-1 min-w-0 truncate text-xs text-muted" title={model}>
            {model || t('sidebar.disconnected')}
          </span>
          <ChevronsUpDown size={14} className="shrink-0 text-muted" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-56">
        <DropdownMenuItem onClick={onOpenSettings}>
          <Settings />
          {t('menu.settings')}
        </DropdownMenuItem>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <Globe />
            {t('menu.language')}
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            {LANGS.map((l) => (
              <DropdownMenuItem key={l.code} onClick={() => setLang(l.code)}>
                <span className="flex-1">{l.label}</span>
                {l.code === lang && <Check className="text-primary" />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function SessionRow({
  session,
  active,
  state,
  disabled,
  onSelect,
  onPin,
  onRename,
  onDelete,
}: {
  session: SessionMeta
  active: boolean
  state?: 'running' | 'attention'
  disabled: boolean
  onSelect: (threadId: string) => void
  onPin: (threadId: string, pinned: boolean) => void
  onRename: (threadId: string, title: string) => void
  onDelete: (session: SessionMeta) => void
}) {
  const { t } = useI18n()
  const [renaming, setRenaming] = useState(false)
  const name = session.title || session.first_message || t('sidebar.untitled')

  if (renaming) {
    return (
      <RenameInput
        initial={name}
        onResolve={(title) => {
          setRenaming(false)
          if (title !== null) onRename(session.thread_id, title)
        }}
      />
    )
  }

  return (
    <div className="group relative">
      <button
        onClick={() => onSelect(session.thread_id)}
        disabled={disabled}
        title={session.first_message}
        className={`block w-full text-left pl-3 pr-8 py-2 rounded-lg truncate text-sm transition disabled:opacity-50 ${
          active ? 'bg-surface text-ink' : 'text-muted hover:bg-surface/60 hover:text-ink'
        }`}
      >
        {session.pinned && (
          <Pin size={11} className="inline-block mr-1.5 -mt-0.5 text-primary/70" />
        )}
        {name}
      </button>
      {/* 活动圆点：处理中=脉冲，等你处理=常亮。悬停时让位给 ⋮ 菜单按钮 */}
      {state && (
        <span
          title={state === 'attention' ? t('sidebar.needsYou') : t('sidebar.processing')}
          className={`pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 size-1.5 rounded-full bg-primary transition-opacity group-hover:opacity-0 ${
            state === 'running' ? 'animate-pulse' : ''
          }`}
        />
      )}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            aria-label={t('sidebar.sessionActions')}
            className="absolute right-1 top-1/2 -translate-y-1/2 size-6 grid place-items-center rounded-md text-muted hover:bg-line/30 hover:text-ink transition opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 outline-none"
          >
            <MoreVertical size={15} />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-44">
          <DropdownMenuItem onClick={() => onPin(session.thread_id, !session.pinned)}>
            {session.pinned ? <PinOff /> : <Pin />}
            {session.pinned ? t('sidebar.unpin') : t('sidebar.pin')}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setRenaming(true)}>
            <Pencil />
            {t('sidebar.rename')}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={() => onDelete(session)}>
            <Trash2 />
            {t('sidebar.delete')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

// 内联重命名输入框：Enter 提交，Escape 取消，失焦提交；单次解析避免重复触发。
function RenameInput({
  initial,
  onResolve,
}: {
  initial: string
  onResolve: (title: string | null) => void
}) {
  const [value, setValue] = useState(initial)
  const ref = useRef<HTMLInputElement>(null)
  const done = useRef(false)

  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])

  const finish = (commit: boolean) => {
    if (done.current) return
    done.current = true
    onResolve(commit ? value.trim() : null)
  }

  return (
    <input
      ref={ref}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        // 输入法选字回车不应提交重命名
        if (e.nativeEvent.isComposing) return
        if (e.key === 'Enter') {
          e.preventDefault()
          finish(true)
        } else if (e.key === 'Escape') {
          e.preventDefault()
          finish(false)
        }
      }}
      onBlur={() => finish(true)}
      className="w-full px-3 py-2 rounded-lg text-sm bg-surface text-ink border border-primary/40 outline-none"
    />
  )
}

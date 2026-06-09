import { useEffect, useRef, useState } from 'react'
import { MoreVertical, Pin, PinOff, Pencil, Trash2, type LucideIcon } from 'lucide-react'
import type { ConnState } from '../gateway'
import type { SessionMeta } from '../types'
import type { Theme } from '../theme'

const CONN_DOT: Record<ConnState, string> = {
  connecting: 'bg-accent',
  open: 'bg-success',
  closed: 'bg-error',
}

const displayName = (s: SessionMeta) => s.title || s.first_message || '新对话'

export function Sidebar({
  sessions,
  currentThread,
  conn,
  model,
  theme,
  disabled,
  onSelect,
  onNew,
  onToggleTheme,
  onPin,
  onRename,
  onDelete,
}: {
  sessions: SessionMeta[]
  currentThread: string
  conn: ConnState
  model: string
  theme: Theme
  disabled: boolean
  onSelect: (threadId: string) => void
  onNew: () => void
  onToggleTheme: () => void
  onPin: (threadId: string, pinned: boolean) => void
  onRename: (threadId: string, title: string) => void
  onDelete: (session: SessionMeta) => void
}) {
  return (
    <aside className="w-64 shrink-0 bg-canvas border-r border-line/20 flex flex-col">
      <div className="h-9 app-drag shrink-0" />
      <div className="px-3 pb-3">
        <button
          onClick={onNew}
          disabled={disabled}
          className="no-drag w-full flex items-center gap-2 px-3 py-2 rounded-xl hover:bg-surface transition text-sm disabled:opacity-40"
        >
          <span className="text-accent text-base leading-none">＋</span>
          新对话
        </button>
      </div>

      <div className="flex-1 overflow-auto px-2">
        {sessions.length > 0 && (
          <div className="text-xs text-muted px-3 pt-2 pb-1.5">最近</div>
        )}
        {sessions.map((s) => (
          <SessionRow
            key={s.thread_id}
            session={s}
            active={s.thread_id === currentThread}
            disabled={disabled}
            onSelect={onSelect}
            onPin={onPin}
            onRename={onRename}
            onDelete={onDelete}
          />
        ))}
      </div>

      <div className="px-4 py-3 border-t border-line/20 flex items-center gap-2 text-xs text-muted">
        <span className={`size-1.5 rounded-full ${CONN_DOT[conn]}`} />
        <span className="truncate flex-1">{model || '未连接'}</span>
        <button
          onClick={onToggleTheme}
          title={theme === 'dark' ? '切换到亮色' : '切换到暗色'}
          className="shrink-0 size-6 grid place-items-center rounded-md hover:bg-surface hover:text-ink transition"
        >
          {theme === 'dark' ? '☀' : '☾'}
        </button>
      </div>
    </aside>
  )
}

function SessionRow({
  session,
  active,
  disabled,
  onSelect,
  onPin,
  onRename,
  onDelete,
}: {
  session: SessionMeta
  active: boolean
  disabled: boolean
  onSelect: (threadId: string) => void
  onPin: (threadId: string, pinned: boolean) => void
  onRename: (threadId: string, title: string) => void
  onDelete: (session: SessionMeta) => void
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)

  if (renaming) {
    return (
      <RenameInput
        initial={displayName(session)}
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
          <Pin size={11} className="inline-block mr-1.5 -mt-0.5 text-accent/70" />
        )}
        {displayName(session)}
      </button>
      <button
        ref={triggerRef}
        onClick={() => setMenuOpen((o) => !o)}
        aria-label="会话操作"
        className={`absolute right-1 top-1/2 -translate-y-1/2 size-6 grid place-items-center rounded-md text-muted hover:bg-line/30 hover:text-ink transition ${
          menuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
        }`}
      >
        <MoreVertical size={15} />
      </button>
      {menuOpen && triggerRef.current && (
        <RowMenu
          anchor={triggerRef.current}
          pinned={session.pinned}
          onClose={() => setMenuOpen(false)}
          onPin={() => onPin(session.thread_id, !session.pinned)}
          onRename={() => setRenaming(true)}
          onDelete={() => onDelete(session)}
        />
      )}
    </div>
  )
}

// 固定定位的下拉菜单：从触发按钮的视口矩形计算位置，避开侧栏 overflow 裁剪。
function RowMenu({
  anchor,
  pinned,
  onClose,
  onPin,
  onRename,
  onDelete,
}: {
  anchor: HTMLElement
  pinned: boolean
  onClose: () => void
  onPin: () => void
  onRename: () => void
  onDelete: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  const rect = anchor.getBoundingClientRect()
  const top = Math.min(rect.bottom + 4, window.innerHeight - 132)
  const left = Math.min(rect.left, window.innerWidth - 184)

  return (
    <div
      ref={ref}
      style={{ position: 'fixed', top, left }}
      className="z-50 w-44 bg-surface border border-line rounded-xl shadow-2xl py-1 text-sm"
    >
      <MenuItem
        icon={pinned ? PinOff : Pin}
        label={pinned ? '取消置顶' : '置顶'}
        onClick={() => {
          onPin()
          onClose()
        }}
      />
      <MenuItem
        icon={Pencil}
        label="重命名"
        onClick={() => {
          onRename()
          onClose()
        }}
      />
      <div className="my-1 border-t border-line/40" />
      <MenuItem
        icon={Trash2}
        label="删除"
        danger
        onClick={() => {
          onDelete()
          onClose()
        }}
      />
    </div>
  )
}

function MenuItem({
  icon: Icon,
  label,
  danger,
  onClick,
}: {
  icon: LucideIcon
  label: string
  danger?: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition hover:bg-canvas/60 ${
        danger ? 'text-error' : 'text-ink/90'
      }`}
    >
      <Icon size={14} className="shrink-0" />
      {label}
    </button>
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
        if (e.key === 'Enter') {
          e.preventDefault()
          finish(true)
        } else if (e.key === 'Escape') {
          e.preventDefault()
          finish(false)
        }
      }}
      onBlur={() => finish(true)}
      className="w-full px-3 py-2 rounded-lg text-sm bg-surface text-ink border border-accent/40 outline-none"
    />
  )
}

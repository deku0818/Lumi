import { memo, useEffect, useRef, useState, type ReactNode } from 'react'
import {
  AlertTriangle,
  ChevronRight,
  Clock,
  Folder,
  MoreVertical,
  Pin,
  PinOff,
  Pencil,
  Trash2,
  Settings,
  Globe,
  Check,
  ChevronsUpDown,
  Plus,
  RotateCw,
  Search,
  Send,
  User,
  Users,
  WifiOff,
  X,
} from 'lucide-react'
import type { ConnState } from '../gateway'
import type { ChannelInfo, CronJob, SessionMeta } from '../types'
import { basename, machineColor, machineName, sessionKey, beOf } from '@/lib/utils'
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
  failed: 'bg-error',
}

type Machine = { id: string; name: string; enabled?: boolean }
const CAP = 5 // 每个项目分组默认显示的会话数（置顶/进行中不计入，永远显示）

// 会话前端身份 = backend + thread_id（与 App 的 store/activity key 同源）；飞书群在
// 本地/远程 thread 同名，只用 thread_id 会串号，故一律取复合 key。
const keyOf = (s: SessionMeta) => sessionKey(beOf(s), s.thread_id)

const projName = (dir: string) => (dir ? basename(dir) : '默认')

// 折叠态持久化到 localStorage：返回 [record, toggle]，机器段 / 项目段共用。
function usePersistedToggle(key: string): [Record<string, boolean>, (k: string) => void] {
  const [map, setMap] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem(key) || '{}')
    } catch {
      return {}
    }
  })
  const toggle = (k: string) =>
    setMap((c) => {
      const n = { ...c, [k]: !c[k] }
      localStorage.setItem(key, JSON.stringify(n))
      return n
    })
  return [map, toggle]
}

// 某台机器的会话按项目（workspace_dir）分组；当前项目排最前。渠道会话不进项目组
// （A2：渠道身份优先于项目身份，另起机器级「飞书」分组）。
function projectGroupsFor(sessions: SessionMeta[], backend: string, currentDir: string) {
  const mine = sessions.filter((s) => (s.backend || 'local') === backend && !s.channel)
  const map = new Map<string, SessionMeta[]>()
  for (const s of mine) {
    const dir = s.workspace_dir || ''
    const list = map.get(dir)
    if (list) list.push(s)
    else map.set(dir, [s])
  }
  return [...map.entries()]
    .map(([dir, list]) => ({ dir, name: projName(dir), sessions: list }))
    .sort((a, b) => (a.dir === currentDir ? -1 : b.dir === currentDir ? 1 : 0))
}

// 置顶优先，再按最近活跃（created_at）倒序 —— 「最近」流与筛选结果共用。
const byRecency = (a: SessionMeta, b: SessionMeta) =>
  (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0) ||
  (Date.parse(b.created_at || '') || 0) - (Date.parse(a.created_at || '') || 0)

// 搜索命中高亮（首个匹配段标金）
function highlight(text: string, q: string): ReactNode {
  const i = text.toLowerCase().indexOf(q.toLowerCase())
  if (i < 0) return text
  return (
    <>
      {text.slice(0, i)}
      <span className="text-primary font-medium">{text.slice(i, i + q.length)}</span>
      {text.slice(i + q.length)}
    </>
  )
}

// memo：App 在流式期间每个 token 重渲染，侧栏的 props 全部保持稳定身份，让侧栏不陪跑。
export const Sidebar = memo(function Sidebar({
  width,
  sessions,
  machines,
  machineConn,
  channels,
  recentLimit,
  workspaceDir,
  currentKey,
  conn,
  model,
  activity,
  projectsActive,
  scheduledActive,
  cronJobs,
  cronUnread,
  cronRunning,
  activeCronJob,
  onOpenCronJob,
  onSelect,
  onNew,
  onNewChat,
  onReconnectMachine,
  onOpenProjects,
  onOpenScheduled,
  onOpenSettings,
  onPin,
  onRename,
  onDelete,
}: {
  width: number
  sessions: SessionMeta[]
  machines: Machine[]
  machineConn: Record<string, ConnState>
  channels: Record<string, ChannelInfo[]> // 机器 id → IM 渠道列表（飞书组头状态灯/绑定项目）
  recentLimit: number
  workspaceDir: string
  currentKey: string
  conn: ConnState
  model: string
  activity: Record<string, 'running' | 'attention'>
  projectsActive: boolean
  scheduledActive: boolean
  cronJobs: CronJob[]
  cronUnread: Record<string, number>
  cronRunning: string[]
  activeCronJob: string | null
  onOpenCronJob: (jobId: string) => void
  onSelect: (threadId: string, backend: string) => void
  onNew: () => void
  onNewChat: (backend: string) => void
  onReconnectMachine: (backend: string) => void
  onOpenProjects: () => void
  onOpenScheduled: () => void
  onOpenSettings: () => void
  onPin: (threadId: string, backend: string, pinned: boolean) => void
  onRename: (threadId: string, backend: string, title: string) => void
  onDelete: (session: SessionMeta) => void
}) {
  const { t } = useI18n()
  const [tab, setTab] = useState<'recent' | 'all'>(
    () => (localStorage.getItem('lumi-sidebar-tab') as 'recent' | 'all') || 'recent',
  )
  const setTabP = (v: 'recent' | 'all') => {
    localStorage.setItem('lumi-sidebar-tab', v)
    setTab(v)
  }
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [collapsedM, toggleM] = usePersistedToggle('lumi-sidebar-mcol')
  const [collapsedP, toggleP] = usePersistedToggle('lumi-sidebar-pcol')

  // 禁用（已配置但不连接）的机器从侧栏隐藏；machineColor 仍用全量 machines 保持配色稳定
  const visibleMachines = machines.filter((m) => m.enabled !== false)
  const multi = visibleMachines.length > 1
  const dispName = (s: SessionMeta) => s.title || s.first_message || t('sidebar.untitled')
  const q = query.trim()
  const filtering = !!q
  // 多机时行尾一粒机器色点（仅用颜色标机器，不再写「机器·项目」文字）
  const dotOf = (s: SessionMeta) => (multi ? machineColor(s.backend || 'local', machines) : undefined)

  const row = (s: SessionMeta, dotColor?: string) => (
    <SessionRow
      key={keyOf(s)}
      session={s}
      active={keyOf(s) === currentKey}
      state={activity[keyOf(s)]}
      name={dispName(s)}
      dotColor={dotColor}
      dotName={dotColor ? machineName(s.backend || 'local', machines) : undefined}
      query={q}
      onSelect={onSelect}
      onPin={onPin}
      onRename={onRename}
      onDelete={onDelete}
    />
  )

  // 全部 · 某项目分组：标题 + 限量会话 + 「显示全部 / 收起」
  const renderProject = (backend: string, pg: { dir: string; name: string; sessions: SessionMeta[] }) => {
    const key = `${backend}::${pg.dir}`
    const collapsed = !!collapsedP[key]
    const keep = new Set<string>()
    pg.sessions.forEach((s, i) => {
      if (s.pinned || activity[keyOf(s)] || i < CAP) keep.add(keyOf(s))
    })
    const showAll = expanded[key]
    const shown = showAll ? pg.sessions : pg.sessions.filter((s) => keep.has(keyOf(s)))
    const hidden = pg.sessions.length - shown.length
    return (
      <div key={key}>
        <button
          onClick={() => toggleP(key)}
          className="w-full flex items-center px-2 pt-1.5 pb-0.5 text-left text-[10.5px] uppercase tracking-wide text-muted-foreground hover:text-ink transition"
        >
          <span className="flex-1 min-w-0 truncate">{pg.name}</span>
        </button>
        {!collapsed && (
          <>
            {shown.map((s) => row(s))}
            {hidden > 0 && (
              <button
                onClick={() => setExpanded((e) => ({ ...e, [key]: true }))}
                className="w-full text-left px-3 py-1 text-[10.5px] text-muted-foreground/55 hover:text-primary transition"
              >
                {t('sidebar.showAll', { n: pg.sessions.length })}
              </button>
            )}
            {showAll && pg.sessions.length > CAP && (
              <button
                onClick={() => setExpanded((e) => ({ ...e, [key]: false }))}
                className="w-full text-left px-3 py-1 text-[10.5px] text-muted-foreground/55 hover:text-primary transition"
              >
                {t('sidebar.showLess')}
              </button>
            )}
          </>
        )}
      </div>
    )
  }

  // 全部 · IM 渠道分组（A2：机器级，组头「飞书 · 绑定项目」+ 渠道状态灯，
  // 状态灯复用 ChannelsPanel 的 .chan-orb 样式）。该机器无渠道会话则整组不渲染；
  // 渠道会话不进项目组（projectGroupsFor 已剔除）。目前仅飞书一个渠道。
  const renderChannelGroup = (backend: string) => {
    const mine = sessions.filter((s) => beOf(s) === backend && s.channel)
    if (!mine.length) return null
    const key = `${backend}::__channel__`
    const collapsed = !!collapsedP[key]
    const info = (channels[backend] ?? []).find((c) => c.name === mine[0].channel)
    const proj = info?.config.workspace ? basename(info.config.workspace) : ''
    return (
      <div key={key}>
        <button
          onClick={() => toggleP(key)}
          className="w-full flex items-center gap-1.5 px-2 pt-1.5 pb-0.5 text-left text-[10.5px] text-muted-foreground hover:text-ink transition"
        >
          <Send size={11} className="shrink-0 opacity-70" />
          <span className="min-w-0 truncate">
            {t('sidebar.feishu')}
            {proj && <span className="opacity-60"> · {proj}</span>}
          </span>
          {info && (
            <span
              className={`chan-orb ${info.status.state} scale-[0.67]`}
              title={info.status.detail}
            />
          )}
        </button>
        {!collapsed && mine.sort(byRecency).map((s) => row(s))}
      </div>
    )
  }

  // 全部 · 机器段：可折叠头(状态光点 + 名 + ＋) + 项目分组
  const renderMachine = (m: Machine) => {
    const collapsed = !!collapsedM[m.id]
    const color = machineColor(m.id, machines)
    const cn = machineConn[m.id]
    const offline = cn === 'closed' || cn === 'failed'
    const groups = projectGroupsFor(sessions, m.id, workspaceDir)
    return (
      <div key={m.id} className={`mt-0.5 ${offline ? 'opacity-60' : ''}`}>
        <div className="flex items-center gap-1.5 px-2 pt-2 pb-0.5">
          <button onClick={() => toggleM(m.id)} className="flex flex-1 min-w-0 items-center gap-1.5 text-left">
            <ChevronRight
              size={11}
              className={`shrink-0 text-muted-foreground transition-transform ${collapsed ? '' : 'rotate-90'}`}
            />
            <span
              className={`shrink-0 size-2 rounded-full ${cn === undefined || cn === 'connecting' ? 'animate-pulse' : ''}`}
              style={
                offline
                  ? { border: '1.5px solid var(--color-separator)', opacity: 0.65 }
                  : { background: color, boxShadow: `0 0 5px ${color}` }
              }
              title={cn ?? 'connecting'}
            />
            <span className="flex-1 truncate text-xs font-semibold text-ink/75">{m.name}</span>
          </button>
          <button
            onClick={() => onNewChat(m.id)}
            title={t('sidebar.newChat')}
            className="shrink-0 grid size-5 place-items-center rounded text-muted-foreground hover:bg-line/30 hover:text-primary transition"
          >
            <Plus size={14} />
          </button>
        </div>
        {!collapsed && renderChannelGroup(m.id)}
        {!collapsed &&
          (groups.length ? (
            groups.map((pg) => renderProject(m.id, pg))
          ) : cn === 'connecting' ? (
            <div className="px-3 py-1.5 text-xs text-muted-foreground/60 animate-pulse">
              {t('sidebar.connecting')}
            </div>
          ) : offline ? (
            // 离线（重连耗尽/退避中）：建会话无意义，改显示离线占位 + 重连
            <div className="flex flex-col items-center gap-2 px-3 py-4 text-center">
              <WifiOff size={22} className="text-separator" />
              <span className="text-xs text-muted-foreground">{t('sidebar.offline')}</span>
              <button
                onClick={() => onReconnectMachine(m.id)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs text-ink transition hover:border-primary hover:text-primary"
              >
                <RotateCw size={13} />
                {t('sidebar.reconnect')}
              </button>
            </div>
          ) : (
            <button
              onClick={() => onNewChat(m.id)}
              className="w-full text-left px-3 py-1.5 text-xs text-muted-foreground/60 hover:text-ink transition"
            >
              {t('sidebar.noSessionsNew')}
            </button>
          ))}
      </div>
    )
  }

  // 内容区：搜索中 → 扁平结果；否则按 tab（最近=扁平时间流 / 全部=分组树）
  let content: ReactNode
  if (filtering) {
    const res = sessions
      .filter((s) => dispName(s).toLowerCase().includes(q.toLowerCase()))
      .sort(byRecency)
    content = res.length ? (
      <>
        <div className="px-3 pt-1 pb-1 text-[11px] text-muted-foreground/55">
          {t('sidebar.results', { n: res.length })}
        </div>
        {res.map((s) => row(s, dotOf(s)))}
      </>
    ) : (
      <div className="px-3 py-8 text-center text-xs text-muted-foreground">{t('sidebar.noMatch')}</div>
    )
  } else if (tab === 'recent') {
    const sorted = [...sessions].sort(byRecency)
    const pinned = sorted.filter((s) => s.pinned)
    const rest = sorted.filter((s) => !s.pinned)
    content = sorted.length ? (
      <>
        {pinned.length > 0 && (
          <>
            <SectionLabel>{t('sidebar.pinned')}</SectionLabel>
            {pinned.map((s) => row(s, dotOf(s)))}
          </>
        )}
        <SectionLabel>{t('sidebar.recent')}</SectionLabel>
        {rest.slice(0, recentLimit).map((s) => row(s, dotOf(s)))}
        {rest.length > recentLimit && (
          <div className="px-3 pt-2 pb-1 text-center text-[11px] text-muted-foreground/55">
            {t('sidebar.recentCapped', { n: recentLimit })}
          </div>
        )}
      </>
    ) : (
      <div className="px-3 py-8 text-center text-xs text-muted-foreground">{t('sidebar.empty')}</div>
    )
  } else {
    const localGroups = projectGroupsFor(sessions, 'local', workspaceDir)
    // 已关闭机器的定时任务不显示（刷新时序可能残留旧 job，按可见机器过滤兜底）
    const visibleIds = new Set(visibleMachines.map((m) => m.id))
    const visibleCron = cronJobs.filter((j) => visibleIds.has(j.backend || 'local'))
    content = (
      <>
        {visibleCron.length > 0 && (
          <CollapsibleGroup label={t('sidebar.scheduled')} storageKey="scheduled">
            {visibleCron.map((job) => (
              <CronJobRow
                key={job.id}
                job={job}
                active={job.id === activeCronJob}
                unread={cronUnread[job.id] ?? 0}
                running={cronRunning.includes(job.name)}
                dotColor={multi ? machineColor(job.backend || 'local', machines) : undefined}
                dotName={multi ? machineName(job.backend || 'local', machines) : undefined}
                onOpen={onOpenCronJob}
              />
            ))}
          </CollapsibleGroup>
        )}
        {multi ? (
          visibleMachines.map(renderMachine)
        ) : (
          <>
            {renderChannelGroup('local')}
            {localGroups.length
              ? localGroups.map((pg) => renderProject('local', pg))
              : !sessions.length && (
                  <div className="px-3 py-8 text-center text-xs text-muted-foreground">
                    {t('sidebar.empty')}
                  </div>
                )}
          </>
        )}
      </>
    )
  }

  return (
    <aside style={{ width }} className="shrink-0 bg-canvas border-r border-line/20 flex flex-col">
      <div className="h-9 app-drag shrink-0" />
      <div className="px-3 pb-2">
        <Button
          variant="ghost"
          onClick={onNew}
          className="no-drag w-full justify-start gap-2 h-auto px-3 py-2 rounded-xl"
        >
          <span className="text-primary text-base leading-none">＋</span>
          {t('sidebar.newChat')}
        </Button>
        <button
          onClick={onOpenProjects}
          className={`no-drag relative w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm transition ${
            projectsActive ? 'bg-surface text-ink' : 'text-muted-foreground hover:bg-surface/60 hover:text-ink'
          }`}
        >
          <Folder size={15} className="shrink-0" />
          {t('sidebar.projects')}
        </button>
        <button
          onClick={onOpenScheduled}
          className={`no-drag relative w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm transition ${
            scheduledActive ? 'bg-surface text-ink' : 'text-muted-foreground hover:bg-surface/60 hover:text-ink'
          }`}
        >
          <Clock size={15} className="shrink-0" />
          {t('sidebar.scheduled')}
        </button>
      </div>

      {/* 最近 / 全部 段式 tab */}
      <div className="mx-2 flex gap-0.5 p-0.5 rounded-lg bg-surface/70">
        {(['recent', 'all'] as const).map((v) => (
          <button
            key={v}
            onClick={() => setTabP(v)}
            className={`flex-1 py-1 rounded-md text-xs transition ${
              tab === v ? 'bg-canvas text-ink font-medium shadow-sm' : 'text-muted-foreground hover:text-ink'
            }`}
          >
            {t(v === 'recent' ? 'sidebar.recent' : 'sidebar.all')}
          </button>
        ))}
      </div>

      {/* 搜索 */}
      <div className="mx-2 mt-2 flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-surface/70 border border-line/50 focus-within:border-primary/40 transition">
        <Search size={14} className="shrink-0 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('sidebar.search')}
          className="flex-1 min-w-0 bg-transparent outline-none text-sm text-ink placeholder:text-muted-foreground/60"
        />
        {query && (
          <button onClick={() => setQuery('')} className="shrink-0 text-muted-foreground hover:text-ink">
            <X size={13} />
          </button>
        )}
      </div>

      <div className="mt-2 flex-1 overflow-y-auto overflow-x-hidden px-2 pb-2">{content}</div>

      <div className="p-2 border-t border-line/20">
        <AccountMenu conn={conn} model={model} onOpenSettings={onOpenSettings} />
      </div>
    </aside>
  )
})

// 区段标题（置顶 / 最近）：浅色弱化的非折叠分隔标签
function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="px-3 pt-2.5 pb-1 text-xs text-muted-foreground/60">{children}</div>
}

// 可折叠分组（定时任务用）：标题浅色弱化，点击收起/展开，状态持久化
function CollapsibleGroup({
  label,
  storageKey,
  children,
}: {
  label: string
  storageKey: string
  children: React.ReactNode
}) {
  const key = `lumi-sidebar-collapsed-${storageKey}`
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(key) === '1')
  const toggle = () => {
    setCollapsed((c) => {
      localStorage.setItem(key, c ? '0' : '1')
      return !c
    })
  }
  return (
    <div>
      <button
        onClick={toggle}
        className="group/header w-full flex items-center gap-1.5 px-3 pt-2 pb-1.5 text-xs text-muted-foreground/60 hover:text-muted-foreground transition"
      >
        <span>{label}</span>
        <ChevronRight
          size={11}
          className={`shrink-0 opacity-0 group-hover/header:opacity-100 transition-all ${collapsed ? '' : 'rotate-90'}`}
        />
      </button>
      {!collapsed && children}
    </div>
  )
}

// 定时任务行：失败 ⚠ + 任务名 + 未读角标（或运行中脉冲点）
function CronJobRow({
  job,
  active,
  unread,
  running,
  dotColor,
  dotName,
  onOpen,
}: {
  job: CronJob
  active: boolean
  unread: number
  running: boolean
  dotColor?: string // 多机时行尾机器色点
  dotName?: string // 色点的机器名（tooltip）
  onOpen: (jobId: string) => void
}) {
  const { t } = useI18n()
  return (
    <button
      onClick={() => onOpen(job.id)}
      className={`w-full flex items-center gap-2 pl-3 pr-2.5 py-2 rounded-lg text-sm transition ${
        active ? 'bg-surface text-ink' : 'text-ink/80 hover:bg-surface/60 hover:text-ink'
      } ${job.enabled ? '' : 'opacity-55'}`}
    >
      {job.consecutive_errors > 0 && <AlertTriangle size={13} className="shrink-0 text-primary" />}
      <span className="flex-1 min-w-0 truncate text-left">{job.name}</span>
      {dotColor && !running && unread === 0 && (
        <span
          className="shrink-0 size-1.5 rounded-full"
          style={{ background: dotColor, boxShadow: `0 0 4px ${dotColor}` }}
          title={dotName}
        />
      )}
      {running ? (
        <span
          title={t('sidebar.processing')}
          className="shrink-0 size-1.5 rounded-full bg-primary animate-pulse"
        />
      ) : (
        unread > 0 && (
          <span className="shrink-0 rounded-md bg-line/40 px-1.5 py-0.5 text-[11px] leading-none text-ink/80">
            {t('cron.newBadge', { n: unread })}
          </span>
        )
      )}
    </button>
  )
}

// 左下角账户入口：向上弹出菜单（设置 / 语言子菜单）。
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
          <span className="flex-1 min-w-0 truncate text-xs text-muted-foreground" title={model}>
            {model || t('sidebar.disconnected')}
          </span>
          <ChevronsUpDown size={14} className="shrink-0 text-muted-foreground" />
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

// 会话行：进行中/待处理光点（行首左侧）+ 置顶 + 名 +（最近/搜索时）机器·项目标 + ⋮ 菜单
function SessionRow({
  session,
  active,
  state,
  name,
  dotColor,
  dotName,
  query,
  onSelect,
  onPin,
  onRename,
  onDelete,
}: {
  session: SessionMeta
  active: boolean
  state?: 'running' | 'attention'
  name: string
  dotColor?: string // 多机时行尾机器色点（仅颜色，无文字）
  dotName?: string // 色点的机器名（tooltip）
  query?: string
  onSelect: (threadId: string, backend: string) => void
  onPin: (threadId: string, backend: string, pinned: boolean) => void
  onRename: (threadId: string, backend: string, title: string) => void
  onDelete: (session: SessionMeta) => void
}) {
  const { t } = useI18n()
  const [renaming, setRenaming] = useState(false)
  const backend = session.backend || 'local'

  if (renaming) {
    return (
      <RenameInput
        initial={name}
        onResolve={(title) => {
          setRenaming(false)
          if (title !== null) onRename(session.thread_id, backend, title)
        }}
      />
    )
  }

  return (
    <div className="group relative">
      <button
        onClick={() => onSelect(session.thread_id, backend)}
        title={session.first_message}
        className={`flex w-full items-center gap-1.5 pl-2.5 pr-8 py-2 rounded-lg text-sm transition ${
          active ? 'bg-surface text-ink' : 'text-ink/80 hover:bg-surface/60 hover:text-ink'
        }`}
      >
        {/* 仅「等你处理」保留提醒点（需你操作）；置顶进段不带 📌、进行中不带脉冲点 */}
        {state === 'attention' && (
          <span
            title={t('sidebar.needsYou')}
            className="shrink-0 size-1.5 rounded-full bg-primary"
          />
        )}
        {/* 渠道会话：群/私聊图标（最近流、搜索结果、飞书分组内统一） */}
        {session.channel &&
          (session.channel_kind === 'p2p' ? (
            <User size={13} className="shrink-0 text-info/80" />
          ) : (
            <Users size={13} className="shrink-0 text-info/80" />
          ))}
        <span className="flex-1 min-w-0 truncate text-left">{query ? highlight(name, query) : name}</span>
        {dotColor && (
          <span
            className="shrink-0 size-1.5 rounded-full transition-opacity group-hover:opacity-0"
            style={{ background: dotColor, boxShadow: `0 0 4px ${dotColor}` }}
            title={dotName}
          />
        )}
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            aria-label={t('sidebar.sessionActions')}
            className="absolute right-1 top-1/2 -translate-y-1/2 size-6 grid place-items-center rounded-md text-muted-foreground hover:bg-line/30 hover:text-ink transition opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 outline-none"
          >
            <MoreVertical size={15} />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-44">
          <DropdownMenuItem onClick={() => onPin(session.thread_id, backend, !session.pinned)}>
            {session.pinned ? <PinOff /> : <Pin />}
            {session.pinned ? t('sidebar.unpin') : t('sidebar.pin')}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setRenaming(true)}>
            <Pencil />
            {t('sidebar.rename')}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          {/* 渠道会话删的是共享 checkpoint（群里下条消息会「失忆」重开），文案如实 */}
          <DropdownMenuItem variant="destructive" onClick={() => onDelete(session)}>
            <Trash2 />
            {session.channel ? t('sidebar.clearSession') : t('sidebar.delete')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

// 内联重命名输入框：Enter 提交，Escape 取消，失焦提交；单次解析避免重复触发。ProjectsPage 复用。
export function RenameInput({
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

import { memo, useEffect, useRef, useState, type JSX, type ReactNode } from 'react'
import { Tooltip as TooltipPrimitive } from 'radix-ui'
import {
  ArrowUpCircle,
  Check,
  ChevronsUpDown,
  Clock,
  Folder,
  FolderClosed,
  FolderOpen,
  Globe,
  Laptop,
  MoreHorizontal,
  PanelLeft,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Settings,
  Trash2,
  User,
  Users,
  WifiOff,
  X,
} from 'lucide-react'
import { useUpdateState } from '../update'
import { ConnectionProgressLink, MachineIcon, MachineMark, ReconnectButton, useMachineConn, useMachineSsh, type MachineMarker } from './MachineTabs'
import { sshBusy } from '../lib/sshStatus'
import type { ChannelInfo, ConnState, Machine, SessionMeta } from '../types'
import { basename, botOfThread, machineColor, machineName, sessionKey, beOf, timeAgo, FLOAT_GAP } from '@/lib/utils'
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
import feishuIcon from '../assets/feishu.svg'

const CONN_DOT: Record<ConnState, string> = {
  connecting: 'bg-primary',
  open: 'bg-success',
  closed: 'bg-error',
  failed: 'bg-error',
}

const CAP = 5 // 默认显示最近 5 条；置顶和活跃会话始终显示。

// 会话前端身份 = backend + thread_id（与 App 的 store/activity key 同源）；飞书群在
// 本地/远程 thread 同名，只用 thread_id 会串号，故一律取复合 key。
const keyOf = (s: SessionMeta) => sessionKey(beOf(s), s.thread_id)

const projName = (dir: string) => (dir ? basename(dir) : '默认')

// 项目折叠态持久化到 localStorage：返回 [record, toggle]。
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

// 会话按项目（workspace_dir）分组，桌面与渠道会话同一条路径归组（渠道会话的 checkpoint
// 同样带 workspace_dir；机器人启用前必须绑项目，故不存在「无项目的飞书会话」这一类）。
// 组内最近会话时间 —— 作为分组排序键，仅在有新会话时变化，点击选中不影响，故侧栏不跳动。
type ProjectGroup = {
  backend: string
  dir: string
  name: string
  recency: number
  sessions: SessionMeta[]
}

function projectGroupsFor(sessions: SessionMeta[], backend: string): ProjectGroup[] {
  const map = new Map<string, ProjectGroup>()
  for (const s of sessions) {
    if (beOf(s) !== backend) continue
    const dir = s.workspace_dir || ''
    let g = map.get(dir)
    if (!g) {
      g = { backend, dir, name: projName(dir), recency: 0, sessions: [] }
      map.set(dir, g)
    }
    g.recency = Math.max(g.recency, Date.parse(s.created_at || '') || 0)
    g.sessions.push(s)
  }
  for (const group of map.values()) group.sessions.sort(byRecency)
  return [...map.values()]
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

function SidebarFold({ open, children }: { open: boolean; children: ReactNode }): JSX.Element {
  return (
    <div className="sidebar-fold" data-open={open} inert={!open}>
      <div className="min-h-0 overflow-hidden">{children}</div>
    </div>
  )
}

// memo：App 在流式期间每个 token 重渲染，侧栏的 props 全部保持稳定身份，让侧栏不陪跑。
export const Sidebar = memo(function Sidebar({
  width,
  open,
  onToggle,
  showTitleDrag,
  sessions,
  loadedBackends,
  machines,
  channels,
  currentKey,
  conn,
  backend,
  workspace,
  activity,
  projectsActive,
  scheduledActive,
  onSelect,
  onNew,
  onNewChat,
  onNewChatIn,
  onOpenProjects,
  onOpenScheduled,
  onOpenSettings,
  onPin,
  onRename,
  onDelete,
}: {
  width: number
  open: boolean
  onToggle: () => void
  showTitleDrag: boolean
  sessions: SessionMeta[]
  loadedBackends: Record<string, true> // 该机器 list_sessions 成功返回过才允许显示「暂无会话」
  machines: Machine[]
  channels: Record<string, ChannelInfo[]> // 机器 id → IM 渠道列表（用于会话来源信息）
  currentKey: string
  conn: ConnState
  backend: string // 当前会话所在机器（底栏显示连接名）
  workspace: string // 当前会话绑定的项目目录（底栏显示项目名）
  activity: Record<string, 'running' | 'attention'>
  projectsActive: boolean
  scheduledActive: boolean
  onSelect: (threadId: string, backend: string) => void
  onNew: () => void
  onNewChat: (backend: string) => void
  onNewChatIn: (backend: string, workspace: string) => void // 项目组头「＋」：在该项目新建
  onOpenProjects: () => void
  onOpenScheduled: () => void
  onOpenSettings: () => void
  onPin: (threadId: string, backend: string, pinned: boolean) => void
  onRename: (threadId: string, backend: string, title: string) => void
  onDelete: (session: SessionMeta) => void
}) {
  const { t } = useI18n()
  const machineConn = useMachineConn()
  const ssh = useMachineSsh()
  const [search, setSearch] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [collapsedP, toggleP] = usePersistedToggle('lumi-sidebar-pcol')
  const query = search.trim()
  const queryLower = query.toLowerCase()

  useEffect(() => {
    if (!open) return
    const focusSearch = (event: KeyboardEvent): void => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', focusSearch)
    return () => window.removeEventListener('keydown', focusSearch)
  }, [open])

  // 禁用（已配置但不连接）的机器从侧栏隐藏；machineColor 仍用全量 machines 保持配色稳定
  const visibleMachines = machines.filter((m) => m.enabled !== false)
  const progressMachines = visibleMachines.filter(m => sshBusy(ssh[m.id]) || ssh[m.id]?.phase === 'error')
  const progressIds = new Set(progressMachines.map(m => m.id))
  const multi = visibleMachines.length > 1
  const dispName = (s: SessionMeta) => s.title || s.first_message || t('sidebar.untitled')
  const matchesSearch = (s: SessionMeta) => dispName(s).toLowerCase().includes(queryLower)
  // 多机时行首一枚机器标记（形状分本地/云端、颜色分是哪一台，不再写「机器·项目」文字）；
  // 会话行与定时任务行同一份，只认 backend 字段
  const markOf = (x: { backend?: string | null }): MachineMarker | undefined =>
    multi
      ? { id: beOf(x), color: machineColor(beOf(x), machines), name: machineName(beOf(x), machines) }
      : undefined

  // 置顶段带机器标记；项目内使用统一的标题对齐线。
  const row = (s: SessionMeta, opts: { machine?: MachineMarker; project?: boolean; query?: string }) => (
    <SessionRow
      key={keyOf(s)}
      session={s}
      active={keyOf(s) === currentKey}
      state={activity[keyOf(s)]}
      name={dispName(s)}
      environment={beOf(s) === 'local' ? t('backends.local') : machineName(beOf(s), machines)}
      channelName={s.channel ? botOfThread(channels[beOf(s)] ?? [], s.thread_id)?.config.name : undefined}
      {...opts}
      onSelect={onSelect}
      onPin={onPin}
      onRename={onRename}
      onDelete={onDelete}
    />
  )

  // 搜索时展开命中的项目，不改写用户保存的折叠状态。
  const renderProject = (pg: ProjectGroup) => {
    const key = `${pg.backend}::${pg.dir}`
    const collapsed = !!collapsedP[key]
    const showAll = expanded[key]
    const shown = showAll
      ? pg.sessions
      : pg.sessions.filter((s, i) => i < CAP || s.pinned || !!activity[keyOf(s)])
    const hidden = pg.sessions.length - shown.length
    const hits = query ? pg.sessions.filter(matchesSearch) : null
    if (hits && !hits.length) return null
    const projectOpen = !collapsed || !!query
    const ProjectFolder = projectOpen ? FolderOpen : FolderClosed
    const projectStates = pg.sessions.map((s) => activity[keyOf(s)])
    const projectState = (['attention', 'running'] as const).find((state) => projectStates.includes(state))
    return (
      <div key={key} className="sidebar-project" data-open={projectOpen}>
        <div className="sidebar-project-head">
          <button onClick={() => toggleP(key)} className="sidebar-project-toggle" aria-expanded={projectOpen}>
            <ProjectFolder size={14} className="shrink-0 text-muted-foreground" />
            <span className="min-w-0 truncate font-medium">{pg.name}</span>
            {!projectOpen && projectState && (
              <span className="sidebar-project-state" data-state={projectState} title={t(projectState === 'running' ? 'bg.running' : 'sidebar.needsYou')} />
            )}
            {pg.backend !== 'local' && (
              <span className="sidebar-project-machine">
                <MachineIcon id={pg.backend} size={11} />
                <span className="truncate">{machineName(pg.backend, machines)}</span>
              </span>
            )}
          </button>
          <button
            onClick={() => onNewChatIn(pg.backend, pg.dir)}
            title={t('sidebar.newChat')}
            className="sidebar-project-add"
          >
            <Plus size={13} />
          </button>
        </div>
        <SidebarFold open={projectOpen}>
          <div className="sidebar-project-body">
            {hits ? hits.map((s) => row(s, { project: true, query })) : (
              <>
                {shown.map((s) => row(s, { project: true }))}
                {hidden > 0 && (
                  <button
                    onClick={() => setExpanded((e) => ({ ...e, [key]: true }))}
                    className="sidebar-show-more"
                  >
                    {t('sidebar.showAll', { n: pg.sessions.length })}
                  </button>
                )}
                {showAll && pg.sessions.length > CAP && (
                  <button
                    onClick={() => setExpanded((e) => ({ ...e, [key]: false }))}
                    className="sidebar-show-more"
                  >
                    {t('common.showLess')}
                  </button>
                )}
              </>
            )}
          </div>
        </SidebarFold>
      </div>
    )
  }

  // 没有任何项目组的机器才需要一行占位：离线 / 确凿空态 / 连接中（有组的机器由组头图标说明身份）
  const renderMachinePlaceholder = (m: Machine) => {
    const cn = machineConn[m.id]
    const offline = cn === 'closed' || cn === 'failed'
    const head = (
      <div className="flex items-center gap-1.5 pl-1 pt-2.5 pb-1 text-xs text-muted-foreground/80">
        <MachineIcon id={m.id} size={14} />
        <span className="min-w-0 truncate">{m.name}</span>
      </div>
    )
    return (
      <div key={m.id}>
        {multi && head}
        {offline ? (
          // 离线（重连耗尽/退避中）：建会话无意义，改显示离线占位 + 重连
          <div className="flex flex-col items-center gap-2 px-3 py-4 text-center">
            <WifiOff size={22} className="text-separator" />
            <span className="text-xs text-muted-foreground">{t('sidebar.offline')}</span>
            <ReconnectButton id={m.id} label={t('sidebar.reconnect')} />
          </div>
        ) : cn === 'open' && loadedBackends[m.id] ? (
          // 确凿的空态：连接就绪且该机器的列表成功返回过（未返回前显示连接中，
          // 别把「首拉还没到手/失败」渲染成「没有会话」）
          <button
            onClick={() => onNewChat(m.id)}
            className="w-full text-left px-3 py-1.5 text-xs text-muted-foreground/60 hover:text-ink transition"
          >
            {t('sidebar.noSessionsNew')}
          </button>
        ) : (
          <div className="px-3 py-1.5 text-xs text-muted-foreground/60 animate-pulse">{t('common.connecting')}</div>
        )}
      </div>
    )
  }

  // 置顶段 + 项目组（跨机器按最近活跃排序）+ 无项目组机器的占位。
  const groups = visibleMachines
    .flatMap((m) => projectGroupsFor(sessions, m.id))
    .sort((a, b) => b.recency - a.recency)
  const withGroups = new Set(groups.map((g) => g.backend))
  const pinned = sessions.filter((s) => s.pinned && matchesSearch(s)).sort(byRecency)
  const projects = groups.map(renderProject).filter((project) => project !== null)
  const noMatch = query && !pinned.length && !projects.length
  const content = (
    <>
      {progressMachines.map(m => <div key={m.id} className="mb-2">
        <div className="flex items-center gap-1.5 px-2.5 pt-2 pb-1 text-xs text-muted-foreground">
          <MachineMark id={m.id} size={14} /><span className="truncate">{m.name}</span>
        </div>
        <ConnectionProgressLink id={m.id} />
      </div>)}
      {pinned.length > 0 && (
        <>
          <SectionLabel>{t('sidebar.pinned')}</SectionLabel>
          {pinned.map((s) => row(s, { machine: markOf(s), query }))}
        </>
      )}
      {noMatch ? <div className="px-3 py-3 text-center text-xs text-muted-foreground">{t('sidebar.noMatch')}</div> : projects}
      {!query && visibleMachines.filter((m) => !withGroups.has(m.id) && !progressIds.has(m.id)).map(renderMachinePlaceholder)}
    </>
  )

  return (
    <aside
      style={{ width, left: FLOAT_GAP, top: FLOAT_GAP, bottom: FLOAT_GAP }}
      className={`absolute z-10 sidebar-float rounded-panel flex flex-col overflow-hidden transition-[translate,opacity,visibility] duration-300 ease-out ${
        open ? '' : '-translate-x-[110%] opacity-0 invisible pointer-events-none'
      }`}
    >
      {/* 顶行：mac 红绿灯占左侧（drag 区、h-9 中心线对齐灯位 y=28），收起按钮靠右。
          -mt-px 抵消面板 1px 描边，使按钮与红绿灯中心线像素级对齐。
          app-drag 仅展开时生效：拖拽区域按布局盒计算、无视 transform/opacity，收起的
          侧栏布局盒仍在原位（aside z-10 绘制在后），残留 drag 矩形会盖回收起态展开钮的挖洞 */}
      <div className={`h-9 -mt-px shrink-0 flex items-center justify-end pr-1.5 ${showTitleDrag && open ? 'app-drag' : ''}`}>
        {/* icon-sm(28px) 与收起态展开钮 / 右栏收放钮同尺寸同中心线（y≈27）。
            open 翻转时交替 toggle-fade-in 变体重触发动画（换 animation-name 即重放，
            不重挂 DOM 不丢焦点）：250ms 隐身期让按钮不跟着面板滑动横穿红绿灯区，
            面板到位（或淡没）后才原地现身——与右栏收放钮同节奏 */}
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onToggle}
          title={t('sidebar.collapse')}
          className={`${open ? 'toggle-fade-in' : 'toggle-fade-in-alt'} no-drag -translate-y-px text-muted-foreground hover:text-ink`}
        >
          <PanelLeft />
        </Button>
      </div>
      <div className="sidebar-navigation">
        <Button
          variant="ghost"
          onClick={onNew}
          className="no-drag w-full justify-start gap-2 h-[34px] px-2.5 rounded-lg"
        >
          <Plus size={16} className="text-primary" />
          {t('sidebar.newChat')}
        </Button>
        <div className="sidebar-utility">
          <button
            onClick={onOpenProjects}
            className={`no-drag relative w-full flex items-center gap-2 px-2.5 h-8 rounded-lg text-xs transition ${
              projectsActive ? 'bg-surface text-ink' : 'text-muted-foreground hover:bg-surface/60 hover:text-ink'
            }`}
          >
            <Folder size={15} className="shrink-0" />
            {t('sidebar.projects')}
          </button>
          <button
            onClick={onOpenScheduled}
            className={`no-drag relative w-full flex items-center gap-2 px-2.5 h-8 rounded-lg text-xs transition ${
              scheduledActive ? 'bg-surface text-ink' : 'text-muted-foreground hover:bg-surface/60 hover:text-ink'
            }`}
          >
            <Clock size={15} className="shrink-0" />
            {t('sidebar.scheduled')}
          </button>
        </div>
        <div className="sidebar-search">
          <Search size={13} className="shrink-0" />
          <input
            ref={searchRef}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Escape') setSearch('') }}
            placeholder={t('sidebar.search')}
            aria-label={t('sidebar.search')}
          />
          {search && (
            <button onClick={() => { setSearch(''); searchRef.current?.focus() }} aria-label={t('common.close')}>
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      <div className="sidebar-scroll flex-1 overflow-y-auto overflow-x-hidden">{content}</div>

      <div className="p-2 border-t border-line/20 space-y-1.5">
        <UpdateBar />
        <AccountMenu
          conn={conn}
          backend={backend}
          workspace={workspace}
          machines={machines}
          onOpenSettings={onOpenSettings}
        />
      </div>
    </aside>
  )
})

// 更新提示条：只在「此刻能装」时出现——Win/Linux 下载完成（ready），或 macOS 检测到新版
// （available + manual，点击把下载转交浏览器）。检查中/下载中一律静默，不打扰当前操作；
// 想看进度的用户去设置→关于。一静一动：图标呼吸，文字不动。
//
// 必须能忽略：macOS 的 autoDownload=false 让状态永远停在 available，没有任何路径能让这
// 条提示自然消失——不给关闭入口的话，不想升级的用户每次开应用 15 秒后都会再被它占住侧栏。
// 按版本号记忆忽略，下一个版本照常提示；忽略后仍可从设置→关于进入。
const UPDATE_DISMISS_KEY = 'lumi-update-dismissed'

function UpdateBar() {
  const { t } = useI18n()
  const update = useUpdateState()
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(UPDATE_DISMISS_KEY) || '')
  if (!update) return null
  const actionable = update.status === 'ready' || (update.status === 'available' && update.manual)
  if (!actionable || (update.version && update.version === dismissed)) return null
  const dismiss = () => {
    if (!update.version) return
    localStorage.setItem(UPDATE_DISMISS_KEY, update.version)
    setDismissed(update.version)
  }
  return (
    <div className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs text-ink transition bg-primary/10 border border-primary/25 hover:bg-primary/15">
      <button
        onClick={() => window.lumi?.update?.install()}
        className="flex-1 min-w-0 flex items-center gap-2 text-left"
      >
        <ArrowUpCircle size={14} className="shrink-0 text-primary update-breathe" />
        <span className="truncate">{t('update.newVersion', { v: update.version ?? '' })}</span>
        <span className="ml-auto shrink-0 font-semibold text-primary">
          {t(update.status === 'ready' ? 'about.restart' : 'about.download')}
        </span>
      </button>
      <button onClick={dismiss} title={t('common.close')} className="shrink-0 text-muted-foreground hover:text-ink">
        <X size={12} />
      </button>
    </div>
  )
}

// 区段标题（置顶 / 最近）：浅色弱化的非折叠分隔标签
function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="px-3 pt-2.5 pb-1 text-[11px] text-muted-foreground/80">{children}</div>
}

// 左下角账户入口：机器图标（带连接态点）+ 当前项目名 + 靠右连接名；向上弹出菜单（设置 / 语言子菜单）。
function AccountMenu({
  conn,
  backend,
  workspace,
  machines,
  onOpenSettings,
}: {
  conn: ConnState
  backend: string
  workspace: string
  machines: Machine[]
  onOpenSettings: () => void
}) {
  const { t, lang, setLang } = useI18n()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-surface transition text-left outline-none">
          <span className="relative shrink-0 size-6 grid place-items-center rounded-full bg-surface">
            <MachineIcon id={backend} size={14} />
            <span
              className={`absolute -right-0.5 -bottom-0.5 size-2 rounded-full ring-2 ring-canvas ${CONN_DOT[conn]}`}
            />
          </span>
          <span className="flex-1 min-w-0 truncate text-xs text-ink" title={workspace}>
            {workspace ? basename(workspace) : ''}
          </span>
          <span className="shrink min-w-0 max-w-[45%] truncate text-[11px] text-muted-foreground">
            {machineName(backend, machines)}
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

type SessionRowProps = {
  session: SessionMeta
  active: boolean
  state?: 'running' | 'attention'
  name: string
  environment: string
  channelName?: string
  machine?: MachineMarker // 多机时行首机器标记（扁平流用；项目树里机器身份在组头）
  project?: boolean // 项目内会话统一对齐；渠道行在标题左侧保留群/私聊图标
  query?: string
  onSelect: (threadId: string, backend: string) => void
  onPin: (threadId: string, backend: string, pinned: boolean) => void
  onRename: (threadId: string, backend: string, title: string) => void
  onDelete: (session: SessionMeta) => void
}

// 会话行：机器/渠道图标 + 标题；运行标识与菜单共用右侧位置，悬停不改变布局。
function SessionRow({
  session,
  active,
  state,
  name,
  environment,
  channelName,
  machine,
  project,
  query,
  onSelect,
  onPin,
  onRename,
  onDelete,
}: SessionRowProps): JSX.Element {
  const { t, lang } = useI18n()
  const [renaming, setRenaming] = useState(false)
  const [hovered, setHovered] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [tooltipOpen, setTooltipOpen] = useState(false)
  const [titleOverflow, setTitleOverflow] = useState(false)
  const titleClipRef = useRef<HTMLSpanElement>(null)
  const titleTextRef = useRef<HTMLSpanElement>(null)
  const backend = session.backend || 'local'
  const isFeishu = session.channel === 'feishu'
  const sourceName = channelName ? `${t('sidebar.feishu')} · ${channelName}` : t('sidebar.feishu')
  const EnvironmentIcon = backend === 'local' ? Laptop : Globe
  const preview = hovered && !menuOpen && !renaming
  const createdAt = Date.parse(session.created_at)

  useEffect(function syncTitleScroll() {
    const clip = titleClipRef.current
    const text = titleTextRef.current
    if (!clip || !text) return

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    let animation: Animation | undefined
    const restartScroll = function (): void {
      animation?.cancel()
      // 菜单覆盖行尾，滚动终点补偿覆盖宽度，布局本身保持不动。
      const distance = text.scrollWidth - clip.clientWidth + (preview && !state ? 24 : 0)
      setTitleOverflow(distance > 0)
      if (!preview || distance <= 0 || reducedMotion.matches) return
      animation = text.animate(
        [{ transform: 'translateX(0)' }, { transform: `translateX(-${distance}px)` }],
        {
          delay: 650,
          duration: Math.max(1600, (distance / 34) * 1000),
          easing: 'cubic-bezier(.22,.05,.18,1)',
          fill: 'forwards',
        },
      )
    }
    const observer = new ResizeObserver(restartScroll)
    observer.observe(clip)
    observer.observe(text)
    reducedMotion.addEventListener('change', restartScroll)
    restartScroll()
    return function stopTitleScroll(): void {
      observer.disconnect()
      reducedMotion.removeEventListener('change', restartScroll)
      animation?.cancel()
    }
  }, [preview, name, renaming, state])

  function handleMenuOpenChange(open: boolean): void {
    setMenuOpen(open)
    setHovered(false)
    setTooltipOpen(false)
  }

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
    <TooltipPrimitive.Root
      delayDuration={260}
      disableHoverableContent
      open={tooltipOpen && !menuOpen}
      onOpenChange={setTooltipOpen}
    >
      <div
        className="session-row group relative rounded-lg"
        data-project={project}
        data-source={session.channel}
        data-active={active}
        data-state={state}
        data-preview={preview}
        data-title-overflow={titleOverflow}
        data-menu-open={menuOpen}
        onPointerEnter={(event) => {
          if (event.pointerType === 'mouse') setHovered(true)
        }}
        onPointerLeave={() => setHovered(false)}
      >
        <TooltipPrimitive.Trigger asChild>
          <button
            onClick={() => onSelect(session.thread_id, backend)}
            onFocus={() => setHovered(true)}
            onBlur={() => setHovered(false)}
            className={`session-select flex w-full items-center gap-2 rounded-lg text-[13px] transition-colors ${
              active ? 'bg-surface text-ink' : 'text-ink/80'
            }`}
          >
            {machine && <MachineMark id={machine.id} color={machine.color} title={machine.name} />}
            {isFeishu && (
              <span
                className="session-source-icon shrink-0 text-muted-foreground"
                style={{ maskImage: `url(${feishuIcon})` }}
                role="img"
                aria-label={t('sidebar.feishu')}
                title={sourceName}
              />
            )}
            {session.channel &&
              (session.channel_kind === 'p2p' ? (
                <User size={13} className="session-channel-icon shrink-0 text-muted-foreground" />
              ) : (
                <Users size={13} className="session-channel-icon shrink-0 text-muted-foreground" />
              ))}
            <span ref={titleClipRef} className="session-title-clip flex-1 min-w-0 overflow-hidden whitespace-nowrap text-left">
              <span ref={titleTextRef} className="inline-block align-bottom whitespace-nowrap">
                {query ? highlight(name, query) : name}
              </span>
            </span>
          </button>
        </TooltipPrimitive.Trigger>
        {state && (
          <span
            className="session-status"
            data-state={state}
            role="img"
            aria-label={t(state === 'running' ? 'bg.running' : 'sidebar.needsYou')}
          />
        )}
        <DropdownMenu onOpenChange={handleMenuOpenChange}>
          <DropdownMenuTrigger asChild>
            <button
              aria-label={t('sidebar.sessionActions')}
              className="session-actions absolute top-1/2 -translate-y-1/2 size-6 grid place-items-center rounded-md text-muted-foreground hover:text-ink outline-none"
            >
              <MoreHorizontal size={15} />
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
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side="right"
          align="start"
          sideOffset={8}
          collisionPadding={8}
          className="session-hover-card"
        >
          <div className="flex items-start gap-3 mb-1.5">
            <span className="min-w-0 flex-1 break-words text-[13px] leading-[1.45] font-medium">{name}</span>
            {Number.isFinite(createdAt) && (
              <time dateTime={session.created_at} className="shrink-0 pt-0.5 text-[10px] text-muted-foreground">
                {timeAgo(createdAt / 1000, lang)}
              </time>
            )}
          </div>
          <div className="flex items-center gap-3 text-[11px] leading-4 text-muted-foreground">
            <span className="flex min-w-0 items-center gap-[5px]">
              <Folder size={12} className="shrink-0" />
              <span className="truncate">{session.workspace_dir ? basename(session.workspace_dir) : t('projects.default')}</span>
            </span>
            <span className="flex min-w-0 items-center gap-[5px]">
              <EnvironmentIcon size={12} className="shrink-0" />
              <span className="truncate">{environment}</span>
            </span>
          </div>
          {isFeishu && <div className="mt-1 text-[11px] leading-4 text-muted-foreground">{sourceName}</div>}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
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

  // 没改就按取消：失焦即提交会把显示用的占位名（首条消息 / 「新对话」）固化成手动标题，
  // 此后自动标题再也进不来
  const finish = (commit: boolean) => {
    if (done.current) return
    done.current = true
    const v = value.trim()
    onResolve(commit && v !== initial.trim() ? v : null)
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

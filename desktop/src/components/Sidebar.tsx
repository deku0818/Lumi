import { memo, useEffect, useRef, useState, type ReactNode } from 'react'
import {
  ChevronRight,
  Clock,
  Folder,
  MoreVertical,
  PanelLeft,
  Pin,
  PinOff,
  Pencil,
  Trash2,
  Settings,
  Globe,
  Check,
  ChevronsUpDown,
  Plus,
  Search,
  Send,
  User,
  Users,
  WifiOff,
  X,
  ArrowUpCircle,
} from 'lucide-react'
import { useUpdateState } from '../update'
import { MachineIcon, MachineMark, ReconnectButton, useMachineConn, type MachineMarker } from './MachineTabs'
import type { ChannelInfo, ConnState, Machine, SessionMeta } from '../types'
import { basename, botOfThread, machineColor, machineName, sessionKey, beOf, FLOAT_GAP } from '@/lib/utils'
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

const CAP = 5 // 每个项目分组默认显示的会话数（置顶/进行中不计入，永远显示）

// 会话前端身份 = backend + thread_id（与 App 的 store/activity key 同源）；飞书群在
// 本地/远程 thread 同名，只用 thread_id 会串号，故一律取复合 key。
const keyOf = (s: SessionMeta) => sessionKey(beOf(s), s.thread_id)

const projName = (dir: string) => (dir ? basename(dir) : '默认')

// 折叠态持久化到 localStorage：返回 [record, toggle]，项目组 / 飞书子组共用。
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
  desktop: SessionMeta[]
  channel: SessionMeta[]
}

function projectGroupsFor(sessions: SessionMeta[], backend: string): ProjectGroup[] {
  const map = new Map<string, ProjectGroup>()
  for (const s of sessions) {
    if (beOf(s) !== backend) continue
    const dir = s.workspace_dir || ''
    let g = map.get(dir)
    if (!g) {
      g = { backend, dir, name: projName(dir), recency: 0, desktop: [], channel: [] }
      map.set(dir, g)
    }
    g.recency = Math.max(g.recency, Date.parse(s.created_at || '') || 0)
    ;(s.channel ? g.channel : g.desktop).push(s)
  }
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
  channels: Record<string, ChannelInfo[]> // 机器 id → IM 渠道列表（飞书子组头取机器人名）
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
  // 项目内搜索：组头「🔍」打开，一次只开一个项目；query 只筛该项目（含飞书子组）
  const [search, setSearch] = useState<{ key: string; q: string } | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [collapsedP, toggleP] = usePersistedToggle('lumi-sidebar-pcol')

  // 禁用（已配置但不连接）的机器从侧栏隐藏；machineColor 仍用全量 machines 保持配色稳定
  const visibleMachines = machines.filter((m) => m.enabled !== false)
  const multi = visibleMachines.length > 1
  const dispName = (s: SessionMeta) => s.title || s.first_message || t('sidebar.untitled')
  // 多机时行首一枚机器标记（形状分本地/云端、颜色分是哪一台，不再写「机器·项目」文字）；
  // 会话行与定时任务行同一份，只认 backend 字段
  const markOf = (x: { backend?: string | null }): MachineMarker | undefined =>
    multi
      ? { id: beOf(x), color: machineColor(beOf(x), machines), name: machineName(beOf(x), machines) }
      : undefined

  // 扁平流（置顶段）传 machine；项目树传 bullet（+ 搜索态 query）
  const row = (s: SessionMeta, opts: { machine?: MachineMarker; bullet?: boolean; query?: string }) => (
    <SessionRow
      key={keyOf(s)}
      session={s}
      active={keyOf(s) === currentKey}
      state={activity[keyOf(s)]}
      name={dispName(s)}
      {...opts}
      onSelect={onSelect}
      onPin={onPin}
      onRename={onRename}
      onDelete={onDelete}
    />
  )

  // 项目组：组头（机器识别色图标 + 项目名 + 折叠箭头 + 悬停「＋ 🔍」）+ 限量桌面会话
  // + 飞书子组；搜索态下改为输入框 + 该项目内（含飞书）的扁平命中结果。组头字重颜色与区段标签同档，会话标题才是视觉主体；折叠箭头展开态悬停
  // 才现、收起态常显（否则收起的项目与没会话的项目长得一样）。机器只靠图标颜色区分。
  const renderProject = (pg: ProjectGroup) => {
    const key = `${pg.backend}::${pg.dir}`
    const collapsed = !!collapsedP[key]
    const keep = new Set<string>()
    pg.desktop.forEach((s, i) => {
      if (s.pinned || activity[keyOf(s)] || i < CAP) keep.add(keyOf(s))
    })
    const showAll = expanded[key]
    const shown = showAll ? pg.desktop : pg.desktop.filter((s) => keep.has(keyOf(s)))
    const hidden = pg.desktop.length - shown.length
    const searching = search?.key === key
    const q = searching ? search.q.trim() : ''
    const hits = q
      ? [...pg.desktop, ...pg.channel]
          .filter((s) => dispName(s).toLowerCase().includes(q.toLowerCase()))
          .sort(byRecency)
      : null
    return (
      <div key={key}>
        <div className="group/header flex items-center gap-1.5 pl-1 pr-1 pt-2.5 pb-1 text-xs text-muted-foreground/80 hover:text-muted-foreground transition">
          <button onClick={() => toggleP(key)} className="flex flex-1 min-w-0 items-center gap-1.5 text-left">
            <MachineIcon id={pg.backend} size={14} />
            <span className="min-w-0 truncate">{pg.name}</span>
            <ChevronRight
              size={12}
              className={`shrink-0 transition-all ${collapsed ? '' : 'rotate-90 opacity-0 group-hover/header:opacity-100'}`}
            />
          </button>
          <button
            onClick={() => onNewChatIn(pg.backend, pg.dir)}
            title={t('sidebar.newChat')}
            className="shrink-0 grid size-5 place-items-center rounded opacity-0 group-hover/header:opacity-100 hover:bg-line/30 hover:text-ink transition"
          >
            <Plus size={13} />
          </button>
          <button
            onClick={() => setSearch(searching ? null : { key, q: '' })}
            title={t('sidebar.search')}
            className={`shrink-0 grid size-5 place-items-center rounded hover:bg-line/30 hover:text-ink transition ${
              searching ? 'text-ink' : 'opacity-0 group-hover/header:opacity-100'
            }`}
          >
            <Search size={13} />
          </button>
        </div>
        {searching && (
          <div className="mx-1 mb-1 flex items-center gap-2 px-2.5 py-1 rounded-lg bg-surface/70 border border-line/50 focus-within:border-primary/40 transition">
            <input
              autoFocus
              value={search.q}
              onChange={(e) => setSearch({ key, q: e.target.value })}
              onKeyDown={(e) => e.key === 'Escape' && setSearch(null)}
              placeholder={t('sidebar.search')}
              className="flex-1 min-w-0 bg-transparent outline-none text-[13px] text-ink placeholder:text-muted-foreground/60"
            />
            <button onClick={() => setSearch(null)} className="shrink-0 text-muted-foreground hover:text-ink">
              <X size={13} />
            </button>
          </div>
        )}
        {hits ? (
          hits.length ? (
            hits.map((s) => row(s, { bullet: true, query: q }))
          ) : (
            <div className="px-3 py-3 text-center text-xs text-muted-foreground">{t('sidebar.noMatch')}</div>
          )
        ) : !collapsed && (
          <>
            {shown.map((s) => row(s, { bullet: true }))}
            {hidden > 0 && (
              <button
                onClick={() => setExpanded((e) => ({ ...e, [key]: true }))}
                className="w-full text-left px-3 py-1 text-[10.5px] text-muted-foreground/55 hover:text-primary transition"
              >
                {t('sidebar.showAll', { n: pg.desktop.length })}
              </button>
            )}
            {showAll && pg.desktop.length > CAP && (
              <button
                onClick={() => setExpanded((e) => ({ ...e, [key]: false }))}
                className="w-full text-left px-3 py-1 text-[10.5px] text-muted-foreground/55 hover:text-primary transition"
              >
                {t('common.showLess')}
              </button>
            )}
            {renderFeishuSub(pg)}
          </>
        )}
      </div>
    )
  }

  // 项目内飞书子组（可折叠二级组）：「飞书 · 机器人名」+ 缩进一级的会话行。按 thread 前缀
  // 归属机器人各成一组（一个项目一个机器人，多组只在换绑/旧数据时出现）；机器人已删则只显「飞书」。
  const renderFeishuSub = (pg: ProjectGroup) => {
    if (!pg.channel.length) return null
    const chans = channels[pg.backend] ?? []
    const groups = new Map<string, SessionMeta[]>()
    for (const s of pg.channel) {
      const botId = botOfThread(chans, s.thread_id)?.config.id ?? ''
      const arr = groups.get(botId)
      if (arr) arr.push(s)
      else groups.set(botId, [s])
    }
    return [...groups.entries()].map(([botId, group]) => {
      const key = `${pg.backend}::${pg.dir}::feishu::${botId}`
      const collapsed = !!collapsedP[key]
      const bot = chans.find((c) => c.config.id === botId)?.config.name
      return (
        <div key={key}>
          <button
            onClick={() => toggleP(key)}
            className="group/header w-full flex items-center gap-1.5 pl-1.5 pr-2 pt-1.5 pb-0.5 text-left text-[11px] text-muted-foreground/80 hover:text-muted-foreground transition"
          >
            <Send size={11} className="shrink-0" />
            <span className="min-w-0 truncate">
              {t('sidebar.feishu')}
              {bot && <span> · {bot}</span>}
            </span>
            <ChevronRight
              size={11}
              className={`shrink-0 transition-all ${collapsed ? '' : 'rotate-90 opacity-0 group-hover/header:opacity-100'}`}
            />
          </button>
          {!collapsed && <div>{group.sort(byRecency).map((s) => row(s, { bullet: true }))}</div>}
        </div>
      )
    })
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

  // 内容区：置顶段 + 项目组（所有可见机器扁平并列、按最近活跃排序，机器身份由组头图标
  // 颜色承载）+ 无项目组机器的占位。定时任务不进侧栏，走顶部「定时任务」入口。
  const groups = visibleMachines
    .flatMap((m) => projectGroupsFor(sessions, m.id))
    .sort((a, b) => b.recency - a.recency)
  const withGroups = new Set(groups.map((g) => g.backend))
  const pinned = sessions.filter((s) => s.pinned).sort(byRecency)
  const content = (
    <>
      {pinned.length > 0 && (
        <>
          <SectionLabel>{t('sidebar.pinned')}</SectionLabel>
          {pinned.map((s) => row(s, { machine: markOf(s) }))}
        </>
      )}
      {groups.map(renderProject)}
      {visibleMachines.filter((m) => !withGroups.has(m.id)).map(renderMachinePlaceholder)}
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
      <div className="px-2 pb-2">
        <Button
          variant="ghost"
          onClick={onNew}
          className="no-drag w-full justify-start gap-2 h-auto pl-1 pr-2 py-2 rounded-xl"
        >
          <span className="text-primary text-base leading-none">＋</span>
          {t('sidebar.newChat')}
        </Button>
        <button
          onClick={onOpenProjects}
          className={`no-drag relative w-full flex items-center gap-2 pl-1 pr-2 py-2 rounded-xl text-sm transition ${
            projectsActive ? 'bg-surface text-ink' : 'text-muted-foreground hover:bg-surface/60 hover:text-ink'
          }`}
        >
          <Folder size={15} className="shrink-0" />
          {t('sidebar.projects')}
        </button>
        <button
          onClick={onOpenScheduled}
          className={`no-drag relative w-full flex items-center gap-2 pl-1 pr-2 py-2 rounded-xl text-sm transition ${
            scheduledActive ? 'bg-surface text-ink' : 'text-muted-foreground hover:bg-surface/60 hover:text-ink'
          }`}
        >
          <Clock size={15} className="shrink-0" />
          {t('sidebar.scheduled')}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto overflow-x-hidden px-2 pb-2">{content}</div>

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

// 会话行：（多机时）机器标记 + 待处理光点 + 渠道图标 + 名 + ⋮ 菜单
function SessionRow({
  session,
  active,
  state,
  name,
  machine,
  bullet,
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
  machine?: MachineMarker // 多机时行首机器标记（扁平流用；项目树里机器身份在组头）
  bullet?: boolean // 项目树里的行首空心圆点（执行中实心金；当前会话靠行底色区分）；渠道行仍用群/私聊图标
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
    <div className="group relative mb-0.5">
      <button
        onClick={() => onSelect(session.thread_id, backend)}
        title={session.first_message}
        className={`flex w-full items-center gap-2 pl-3 pr-8 py-[5px] rounded-lg text-[13px] transition ${
          active ? 'bg-surface text-ink' : 'text-ink/80 hover:bg-surface/60 hover:text-ink'
        }`}
      >
        {machine && <MachineMark id={machine.id} color={machine.color} title={machine.name} />}
        {bullet && !session.channel && (
          // 圆点左移 3px、右边补回 3px：标题文字位置不变，只拉开点与标题的距离
          <span
            className={`shrink-0 size-1.5 -ml-[3px] mr-[3px] rounded-full border ${state === 'running' ? 'border-primary bg-primary lumi-blink' : 'border-separator'}`}
          />
        )}
        {/* 渠道会话：群/私聊图标（最近流、搜索结果、飞书分组内统一）。-ml 让 13px 图标与
            普通行的圆点同中心线、标题与普通行标题同起点 */}
        {session.channel &&
          (session.channel_kind === 'p2p' ? (
            <User size={13} className="shrink-0 -ml-[7px] text-success/80" />
          ) : (
            <Users size={13} className="shrink-0 -ml-[7px] text-info/80" />
          ))}
        <span className="flex-1 min-w-0 truncate text-left">{query ? highlight(name, query) : name}</span>
        {/* 仅「等你处理」保留提醒点（需你操作）；置顶进段不带 📌、进行中不带脉冲点 */}
        {state === 'attention' && (
          <span
            title={t('sidebar.needsYou')}
            className="shrink-0 size-1.5 rounded-full bg-primary shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent)_22%,transparent)]"
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

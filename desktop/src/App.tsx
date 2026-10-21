import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useReducer,
  useState,
  type ReactNode,
} from 'react'
import {
  FileText,
  ChevronDown,
  Pencil,
  RotateCcw,
  Square,
  Plus,
  Send,
  X,
  PanelLeft,
} from 'lucide-react'
import { getControlConnection, openChatConnection } from './gatewayConnections.ts'
import { captureHistoryScroll, restoreHistoryScroll, type HistoryScrollAnchor } from './historyScroll.ts'
import { dispatchDraft, type Attachment } from './messageDelivery.ts'
import {
  addPending,
  dropPending,
  hydrateHistory,
  nid,
  prependHistory,
  reduceEvent,
  sessionModelPatch,
  userBubble,
  type PendingItem,
  type SessionState,
} from './sessionReducer.ts'
import { useLatest } from './hooks/useLatest'
import { errorMessage } from '@/lib/utils'
import { Gateway } from './gateway'
import { NewChatTarget } from './components/NewChatTarget'
import { HomeBackdrop } from './components/HomeBackdrop'
import { SshPrompts } from './components/SshPrompts'
import type {
  ActiveModel,
  Artifact,
  AttachedFile,
  BgTask,
  BgTaskOutput,
  ChannelInfo,
  ConnError,
  ConnState,
  CronJob,
  ForkPoint,
  Item,
  Machine,
  ModelPointer,
  Project,
  ProjectTarget,
  ProviderProfile,
  SessionMeta,
  SessionModelWire,
  SlashCommand,
  ToolMode,
  WireEvent,
} from './types.ts'
import { EMPTY_BG_OUTPUT } from './types'
import { ApprovalDialog, type Decision } from './components/ApprovalDialog'
import { ClarifyDialog, ASK_CANCELLED } from './components/ClarifyDialog'
import { Sidebar } from './components/Sidebar'
import { MachineIcon, MachinesProvider } from './components/MachineTabs'
import { FileCards, contentUrl } from './components/Artifacts'
import { BgTasksSection } from './components/BgTasksDrawer'
import { PreviewWindow } from './components/PreviewWindow.tsx'
import { emptyPreviewState, previewReducer } from './lib/previewState.ts'
import { CronPage, RunsSection } from './components/CronPage'
import { RightRail } from './components/RightRail'
import { SubagentDetail, SubagentsSection } from './components/Subagents'
import { useSubagents } from './hooks/useSubagents'
import { ResizeHandle, usePersistedFlag, useResizableWidth } from './components/ResizeHandle'
import { ConfirmDialog } from './components/ConfirmDialog'
import { SettingsDialog } from './components/SettingsDialog'
import { ModelPicker } from './components/ModelPicker'
import { ApprovalModePicker } from './components/ApprovalModePicker'
import { ContextMeter } from './components/ContextMeter'
import { ProjectHomePage } from './components/ProjectHomePage'
import { ProjectsPage } from './components/ProjectsPage'
import { DirBrowser } from './components/DirBrowser'
import { CommandMenu } from './components/CommandMenu'
import { Composer } from './components/Composer'
import { AppTitleBar } from './components/AppTitleBar'
import { StatusIndicator, ItemView } from './components/chat/ItemView'
import { CopyButton, EditBubble, ForkButton, HoverActions, IconAction } from './components/chat/MessageActions.tsx'
import { ToolGroup } from './components/chat/ToolViews'
import { groupItems, segKey } from './lib/chatSegments'
import { AgentGroup } from './components/chat/AgentViews'
import { toast } from './components/Toast'
import { isCommandMode, parseCommand, matchCommands } from './slash'
import { clip, basename, botOfThread, machineColor, machineName, sessionKey, keyThread, keyBackend, beOf, FLOAT_GAP } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useTheme } from './theme'
import { useUiFont } from './font'
import { useI18n } from './i18n'

type AssistantItem = Extract<Item, { kind: 'assistant' }>

const replaceBackendTasks = (prev: BgTask[], backend: string, tasks: BgTask[]): BgTask[] => {
  const mine = prev.filter((t) => beOf(t) === backend)
  const next = tasks.map((t) => {
    const tagged = { ...t, backend }
    const old = mine.find((x) => x.task_id === t.task_id)
    return old && JSON.stringify(old) === JSON.stringify(tagged) ? old : tagged
  })
  if (next.length === mine.length && next.every((t, i) => t === mine[i])) return prev
  return [...prev.filter((t) => beOf(t) !== backend), ...next]
}

// 进程级广播事件：与具体会话无关，handleEvent 在 session 路由之前统一处理。
// 远程机器通常没有活跃会话连接、只有一条控制连接，故控制连接也要把这些转给
// handleEvent——否则远程的定时任务/后台任务在界面上永远是静止的。本机会经两条
// 连接各收一次，这些处理器都按机器整段覆盖或自带去重，重复到达幂等。
const PROCESS_EVENTS = new Set(['cron.result', 'cron.running', 'cron.jobs', 'bg_tasks.update', 'mcp.status'])


export default function App() {
  const [store, setStore] = useState<Record<string, SessionState>>({})
  const [active, setActive] = useState('')
  const [conn, setConn] = useState<ConnState>('connecting')
  // 进程级工作目录 = 当前项目（gateway.ready 下发；切项目对整个 app 生效）
  const [workspaceDir, setWorkspaceDir] = useState('')
  // handleEvent（稳定 useCallback）里做 mcp.status 的当前工作区过滤，须经 ref 读最新值
  const workspaceDirRef = useLatest(workspaceDir)
  // null = 该机器列表尚未成功拉到（未连上 / 拉取中 / 失败）——不能渲染成「还没有项目」
  const [projects, setProjects] = useState<Project[] | null>(null)
  // 项目视图作用的机器（方案甲「先选机器」）
  const [projectsMachine, setProjectsMachine] = useState('local')
  // 项目页是被「新建会话」阻断跳转到的（而非用户主动点「项目」标签）时，顶部提示为什么在这里
  const [needProjectHint, setNeedProjectHint] = useState(false)
  const [showNewProject, setShowNewProject] = useState(false)
  const [pendingRemoveProject, setPendingRemoveProject] = useState<Project | null>(null)
  const [newTarget, setNewTarget] = useState<ProjectTarget | null>(null)
  const newTargetRevision = useRef(0)
  const [input, setInput] = useState('')
  const [commands, setCommands] = useState<SlashCommand[]>([])
  const [cmdSel, setCmdSel] = useState(0)
  const [cmdDismissed, setCmdDismissed] = useState(false)
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  // 各机器 list_sessions 是否成功返回过：成功前该机器的空列表显示「连接中」而非
  // 「暂无会话」——ready 时的首拉可能遇瞬时抖动（重连中 / 服务端尚未就绪），
  // 失败不能被渲染成确凿的空态
  const [loadedBackends, setLoadedBackends] = useState<Record<string, true>>({})
  // 方案甲多机：机器列表（本地恒在 + 远程）与各机控制连接状态
  const [machines, setMachines] = useState<Machine[]>([
    { id: 'local', name: '本地' },
  ])
  const [machineConn, setMachineConn] = useState<Record<string, ConnState>>({})
  const [machineErr, setMachineErr] = useState<Record<string, ConnError>>({})
  const [providers, setProviders] = useState<ProviderProfile[]>([])
  // 「新会话默认模型」（providers.active）。**不是**当前会话在用的模型——那个按会话存在
  // store[key].model 里（见 sessionModel）。改这个只影响之后新建的会话。
  const [defaultModel, setDefaultModel] = useState<ActiveModel>({ provider: '', model: '' })
  // auto 审批分类器指针（providers.json 顶级 classifier，空=跟随会话模型）
  const [classifier, setClassifier] = useState<ModelPointer>({})
  // 工具审批模式：随后续 send/run 透传给后端（auto=AI 审批分类器）
  // 新开应用默认 auto（AI 审批）；渠道各按自己配置的 tool_mode，不受此影响
  const [toolMode, setToolMode] = useState<ToolMode>('auto')
  // 活动会话所在机器：ModelPicker 机器标识 + 设置改模型时判断是否需刷新聊天侧。
  // 从复合 active key 派生（机器 id 已编码其中），杜绝与 active 脱同步——任何切换路径
  // （activate / 通知点击等）只要 setActive 就自动带对机器。空/无分隔符归一到 'local'。
  const activeBackend = keyBackend(active) || 'local'
  // 当前会话在远程机器上：文件预览据此选取用通道并隐藏本地打开入口
  const remoteBackend = activeBackend !== 'local'
  const [showSettings, setShowSettings] = useState(false)
  // 当前会话的列表元数据（memo：App 每个流式 token 都重渲染，别每次 O(sessions) 扫）。
  // channel 只消费 wire 字段（服务端 _channel_of 是唯一判定点），非空 = 只读旁观。
  const activeSession = useMemo(
    () => sessions.find((s) => beOf(s) === activeBackend && s.thread_id === keyThread(active)),
    [sessions, activeBackend, active],
  )
  const activeChannel = activeSession?.channel || ''
  // 打开设置时的初始 tab：旁观横幅「渠道设置」直达 channels，常规入口走默认
  const [settingsTab, setSettingsTab] = useState<'channels' | undefined>()
  const openSettings = useCallback(() => {
    setSettingsTab(undefined)
    setShowSettings(true)
  }, [])
  const [pendingDelete, setPendingDelete] = useState<SessionMeta | null>(null)
  const [themePref, setThemePref] = useTheme()
  const [uiFont, setUiFont] = useUiFont()
  const { t } = useI18n()
  const [notify, setNotify] = useState(() => localStorage.getItem('lumi-notify') === '1')
  // 本地图片内联；SSH 附件统一上传并发路径，由 Agent 按现有权限流程读取。
  const [attachments, setAttachments] = useState<Attachment[]>([])
  // 主区视图：聊天 / 项目管理页 / 定时任务管理页 / 任务会话视图（某任务的某次执行对话 + Runs 侧栏）
  const [bgTasks, setBgTasks] = useState<BgTask[]>([]) // 后台任务全量快照（按 thread 过滤展示）
  const [preview, dispatchPreview] = useReducer(previewReducer, emptyPreviewState)
  // 可拖拽边栏宽度（持久化）：左侧会话栏；右栏按是否有预览选择宽度
  const sidebarW = useResizableWidth('lumi-sidebar-width', 256, 200, 420)
  // 悬浮侧栏展开/收起（持久化）
  const [sidebarOpen, toggleSidebar] = usePersistedFlag('lumi-sidebar-open')
  // 统一右栏（执行记录/后台任务模块）开合：chat 与 cronjob 视图共用一份状态与宽度，
  // 收起在哪个视图都保持收起——对用户而言它是同一个部件
  const [railOpen, toggleRail] = usePersistedFlag('lumi-rail-open')
  // 与左侧栏同参（默认 256、拖拽 200-420），左右观感对称；键名带 v2 让早期 320 的
  // 旧存值失效回默认（旧键 lumi-rail-width 未发布过，只存在于开发机）
  const railW = useResizableWidth('lumi-rail-width-v2', 256, 200, 420)
  const previewW = useResizableWidth('lumi-preview-width', 440, 360, 920)
  const [view, setView] = useState<'chat' | 'newchat' | 'projects' | 'project' | 'scheduled' | 'cronjob'>('newchat')
  // 项目主页当前查看的项目（view='project' 时有效）
  const [projectHome, setProjectHome] = useState<{ backend: string; path: string } | null>(null)
  // 模型菜单 / 斜杠命令 / 输入框连接态的目标：项目主页随主页（那里还没有会话），聊天页随
  // 活动会话。各回调按 ref 判废：请求在途时目标变了，晚到的旧响应丢弃
  let homeTarget: { backend: string; path: string } | null = null
  if (view === 'project') homeTarget = projectHome
  else if (view === 'newchat') homeTarget = newTarget
  const pickerBackend = homeTarget ? homeTarget.backend : activeBackend
  const pickerRef = useLatest(pickerBackend)
  const cmdTargetRef = useLatest(homeTarget ? sessionKey(homeTarget.backend, homeTarget.path) : active)
  // 运行中任务：机器 → 该机器正在执行的 job id。按机器分段——每台机器各发各的进程级快照
  const [cronRunning, setCronRunning] = useState<Record<string, string[]>>({})
  // 运行中的 run（含 thread_id）：机器 → 活条目。cronRunning 从这里派生，另供执行记录
  // 顶部显示可点进观测的活条目、以及观测视图的运行态门控
  const [cronActiveRuns, setCronActiveRuns] = useState<
    Record<string, { job_id: string; thread_id: string; started_at: string }[]>
  >({})
  const [cronVersion, setCronVersion] = useState(0) // 递增触发 cron 数据刷新
  const [cronJobs, setCronJobs] = useState<CronJob[]>([]) // 侧栏任务分组数据
  const [activeCronJob, setActiveCronJob] = useState<string | null>(null) // 任务会话视图当前任务
  const [cronRunThread, setCronRunThread] = useState<string | null>(null) // 当前选中的执行会话
  // 已查看过的执行会话，持久化。侧栏「N new」与 Runs 栏蓝点同源派生自它：
  // 未读 = 任务的 run_threads 减去本集合，故桌面端离线期间的执行重连后照样算未读
  const [readRuns, setReadRuns] = useState<Record<string, true>>(() => {
    // 未读改为派生后 lumi-cron-unread 不再被读写，顺手清掉老用户的残留
    localStorage.removeItem('lumi-cron-unread')
    try {
      return JSON.parse(localStorage.getItem('lumi-cron-read-runs') || '{}')
    } catch {
      return {}
    }
  })
  const viewRef = useLatest(view)
  const activeCronJobRef = useLatest(activeCronJob)
  // cron 事件经 DesktopDelivery 广播到每条 WS 连接，多会话时同一结果会收到多次，按 key 去重
  const seenCronRef = useRef(new Set<string>())
  const fileInputRef = useRef<HTMLInputElement>(null)
  const connsRef = useRef<Record<string, Gateway>>({})
  const subagents = useSubagents(connsRef.current[active], active, keyThread(active), view === 'chat' || view === 'cronjob', railOpen)
  const activeRef = useLatest(active)
  // activate 调用序号：判废晚到的建连结果（openConnection 悬置期间用户切走后不回拽）
  const activationSeqRef = useRef(0)
  // 会话列表镜像到 ref：切会话时据此查它所属项目（workspace_dir），随 switch 下发让后端切 cwd
  const sessionsRef = useLatest(sessions)
  // 本窗口手动命名过的会话 key：session.title 广播与手动重命名竞态时，
  // 自动标题不得覆盖用户敲的名字（后端侧手动名本就优先，只是事件可能晚到）。
  // 按数据标记而非 RPC 在途时序——广播与 rename 响应帧之间没有顺序保证。
  const renamedRef = useRef<Set<string>>(new Set())
  // 每台机器一条「控制连接」：用于跨机器 fan-out list_sessions / 全局 RPC（非 chat 流）
  const controlConns = useRef<Record<string, Gateway>>({})
  const controlOpening = useRef<Record<string, Promise<Gateway> | undefined>>({})
  // 用户气泡图片缩略图：取用通道同预览面板（本地 lumi-file / 远程 lumi-remote）；远程无该机
  // 连接给 undefined（退回文件胶囊），不回落读本地盘。稳定引用，保住 ItemView 的 memo
  const activeGw = controlConns.current[activeBackend]
  const thumbSrc = useMemo(
    () => (path: string) =>
      remoteBackend && !activeGw ? undefined : contentUrl(path, remoteBackend, activeGw),
    [remoteBackend, activeGw],
  )
  const openPreview = useCallback(function openPreview(file: Artifact): void {
    subagents.close()
    dispatchPreview({ type: 'open', owner: activeRef.current, file })
    if (!railOpen) toggleRail()
  }, [railOpen, toggleRail, subagents.close])
  const closeAllPreviews = useCallback(function closeAllPreviews(): void {
    dispatchPreview({ type: 'close-all' })
  }, [])
  const selectPreview = useCallback(function selectPreview(path: string): void {
    dispatchPreview({ type: 'select', path })
  }, [])
  const closePreviewTab = useCallback(function closePreviewTab(path: string): void {
    dispatchPreview({ type: 'close', path })
  }, [])
  const togglePreviewExpanded = useCallback(function togglePreviewExpanded(): void {
    dispatchPreview({ type: 'toggle-expanded' })
  }, [])
  const toggleRightRail = useCallback(function toggleRightRail(): void {
    dispatchPreview({ type: 'shrink' })
    toggleRail()
  }, [toggleRail])
  const openImage = useCallback(function openImage(file: AttachedFile): void {
    openPreview({ ...file, kind: 'image' })
  }, [openPreview])
  const cronJobsRef = useLatest(cronJobs)
  const refreshCronJobsRef = useRef<((only?: string) => void) | null>(null) // handleEvent 经此按机器刷新
  const scrollRef = useRef<HTMLDivElement>(null)
  const historyScrollRef = useRef<{ sid: string; anchor: HistoryScrollAnchor } | null>(null)
  const historyRequests = useRef(new Set<string>())
  const loadOlderRef = useRef<() => void>(() => {})
  // 聊天流「粘底」：贴底时才跟随流式输出，用户上滚即放手（不再抢界面）。
  // pinnedRef 供自动滚动 effect 同步判定；showJump 驱动「回到底部」浮钮渲染。
  const pinnedRef = useRef(true)
  const [showJump, setShowJump] = useState(false)
  const lastActiveRef = useRef(active) // 与当前 active 不同即说明刚切了会话
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // handleEvent 是 []-依赖的稳定回调，通过 ref 读取最新的 store / 通知开关 / 翻译
  const storeRef = useLatest(store)
  // 供 handleEvent（[]-依赖的稳定回调）在 turn.start 固化会话模型时读取
  const defaultModelRef = useLatest(defaultModel)
  const notifyRef = useLatest(notify)
  const tRef = useLatest(t)
  // MCP 失败 toast 去重（`backend:server:error` → 上次弹出时刻）：配置保存→作废→
  // 重载的连环加载会重播同一失败，60s 内不重复弹
  const mcpToastAtRef = useRef(new Map<string, number>())
  const cronRefreshAtRef = useRef(new Map<string, number>()) // cron.jobs 本机经两条连接各来一次，按机器去重
  // 面板刷新信号合并：同一池的广播每条绑定连接各收一帧，微任务尾只发一次
  const mcpSignalQueuedRef = useRef(false)

  useEffect(() => {
    localStorage.setItem('lumi-cron-read-runs', JSON.stringify(readRuns))
  }, [readRuns])

  // 开启通知：持久化 + 立即发一条测试通知验证（经主进程，见 preload.notify）。
  const toggleNotify = (v: boolean) => {
    setNotify(v)
    localStorage.setItem('lumi-notify', v ? '1' : '0')
    if (v) void window.lumi.notify?.({ title: 'Lumi', body: t('notify.enabled') })
  }

  // 当前活动会话的派生视图
  const cur = store[active]
  const items = cur?.items ?? []
  const pending = cur?.pending ?? []
  const running = cur?.running ?? false
  const runStart = cur?.runStart
  const thinkingText = cur?.thinkingText ?? ''
  const compacting = cur?.compacting ?? false
  // 观测中的运行中 cron run：当前视图正看着一条仍在执行的 cron 线程（cron.running 为
  // 权威来源）。观测期间只读（输入禁用）+ 显示停止键；停止调 stopCronRun（run 在调度器
  // 里、不在本会话 bridge，普通 stop 掐不到）。跑完从 cron.running 移除 → 转 idle 可续聊。
  const liveRun =
    view === 'cronjob' && cronRunThread
      ? (cronActiveRuns[activeBackend] ?? []).find((r) => r.thread_id === cronRunThread)
      : undefined
  const observingCronRun = !!liveRun
  // 渲染队首（最早挂起的那条）；应答后出队，下一条自动浮现
  const approval = cur?.approval?.[0] ?? null
  const clarify = cur?.clarify?.[0] ?? null
  // 本会话生效的模型：后端下发（gateway.ready / switch_session / set_session_model）后
  // 按会话存住，未知时才退到新会话默认。顶部选择器、上下文环分母都读它——读全局默认会
  // 在「改了默认、老会话仍跑旧模型」时显示错的那一个。
  const sessionModel = cur?.model ?? defaultModel
  const model = sessionModel.model
  // 当前会话模型的上下文窗口（tokens）；能力未知时为 0，ContextMeter 自会隐藏。
  // memo 化避免每次流式 token 重渲染都重跑 providers.find。
  const contextWindow = useMemo(
    () =>
      providers.find((p) => p.id === sessionModel.provider)?.context_window?.[
        sessionModel.model
      ] ?? 0,
    [providers, sessionModel.provider, sessionModel.model],
  )


  // 每个会话的活动态，喂给侧栏显示圆点：attention=等你处理（审批/澄清），running=处理中。
  // store 每个流式 token 都换新身份，内容不变时复用上一个对象，避免 Sidebar 每 token 重渲染。
  const activityRef = useRef<Record<string, 'running' | 'attention'>>({})
  const activity = useMemo(() => {
    const m: Record<string, 'running' | 'attention'> = {}
    for (const tid in store) {
      const s = store[tid]
      if (s.approval.length || s.clarify.length) m[tid] = 'attention'
      else if (s.running) m[tid] = 'running'
    }
    const prev = activityRef.current
    const keys = Object.keys(m)
    if (keys.length === Object.keys(prev).length && keys.every((k) => prev[k] === m[k])) {
      return prev
    }
    activityRef.current = m
    return m
  }, [store])

  // 按 session_id 路由事件到对应会话（后台会话的事件也能正确归位）。
  // gateway.ready 不会到达这里——openConnection 的 onEvent 已拦截处理。
  // backend：本连接所属机器 id。会话身份 = sessionKey(backend, thread)，故事件也按此归位——
  // 否则本地/远程同名 thread（飞书群）的事件会串进同一条会话。
  const handleEvent = useCallback((ev: WireEvent, backend: string) => {
    const { type, payload } = ev
    if (type === 'subagent.event') return // The selected observer owns this independent timeline.
    // cron 事件是进程级广播（与会话无关），在 session 路由之前单独处理
    if (type === 'cron.running') {
      // 进程级快照，只替换发来那台机器的那一段（别把其他机器的运行态抹了）。
      // 本机同一份快照会经会话连接 + 控制连接各来一次，内容没变就保持引用不动，
      // 否则每次都换新对象、把 memo 化的 Sidebar 白拖着重渲染一遍
      const runs = payload.runs ?? []
      // 本机同一份快照经会话连接 + 控制连接各来一次，内容没变就保持引用不动，
      // 避免白触发 RunsSection / observingCronRun 消费方重渲染（同 cronRunning 的去重）
      setCronActiveRuns((prev) => {
        const cur = prev[backend]
        const same =
          cur?.length === runs.length &&
          cur.every((r, i) => r.thread_id === runs[i].thread_id && r.job_id === runs[i].job_id)
        return same ? prev : { ...prev, [backend]: runs }
      })
      setCronRunning((prev) => {
        const next = runs.map((r) => r.job_id)
        const cur = prev[backend]
        const same = cur?.length === next.length && cur.every((v, i) => v === next[i])
        return same ? prev : { ...prev, [backend]: next }
      })
      return
    }
    // 任务增删改（agent 工具在会话里建/改/删定时任务）：只重拉来源机器的任务列表
    // （事件带 backend），无需手动 Ctrl+R，也不白拉其他机器。本机同一变更经会话连接 +
    // 控制连接各来一次，200ms 内按机器去重，避免重复 list_cron_jobs（同 mcp.status 去重）。
    if (type === 'cron.jobs') {
      const now = Date.now()
      if (now - (cronRefreshAtRef.current.get(backend) ?? 0) >= 200) {
        cronRefreshAtRef.current.set(backend, now)
        refreshCronJobsRef.current?.(backend)
      }
      return
    }
    // MCP 池后台加载完成：服务端已按连接过滤（只有绑定该池的连接收到），但后台项目
    // 会话的常驻连接仍会收到自己池的失败——失败 toast 只对当前工作区弹（"" = 全局池
    // 恒相关；别的项目的失败在 MCP 面板可见，跨项目弹红是纯噪音），60s 去重；
    // 面板刷新的 window 信号则无条件发（面板可能正浏览任意项目）
    if (type === 'mcp.status') {
      if (!payload.project || payload.project === workspaceDirRef.current) {
        const now = Date.now()
        for (const s of payload.servers ?? []) {
          if (s.ok) continue
          const k = `${backend}:${s.name}:${s.error ?? ''}`
          if (now - (mcpToastAtRef.current.get(k) ?? 0) < 60_000) continue
          mcpToastAtRef.current.set(k, now)
          toast.error(tRef.current('mcp.serverFailed', { name: s.name, error: s.error ?? '' }))
        }
      }
      if (!mcpSignalQueuedRef.current) {
        mcpSignalQueuedRef.current = true
        queueMicrotask(() => {
          mcpSignalQueuedRef.current = false
          window.dispatchEvent(new CustomEvent('lumi:mcp-status'))
        })
      }
      return
    }
    // 后台任务变更：本机进程级快照广播，只替换本机那一段（保留别机的），前端按 thread + backend 过滤展示
    if (type === 'bg_tasks.update') {
      setBgTasks((prev) => replaceBackendTasks(prev, backend, payload.tasks ?? []))
      return
    }
    if (type === 'cron.result') {
      const key = `${payload.job_id}:${payload.started_at}`
      if (seenCronRef.current.has(key)) return
      seenCronRef.current.add(key)
      // 去重集合封顶（Set 按插入序迭代，砍最旧的），避免长驻进程无限增长
      if (seenCronRef.current.size > 500) {
        seenCronRef.current.delete(seenCronRef.current.values().next().value!)
      }
      // 未读不在此累积：重拉来源机器的任务列表即带回最新 run_threads，角标随之派生；
      // cronVersion 驱动 CronPage 重拉执行记录
      refreshCronJobsRef.current?.(backend)
      setCronVersion((v) => v + 1)
      const viewingThisJob =
        viewRef.current === 'cronjob' && activeCronJobRef.current === payload.job_id
      // 正在看该任务的会话视图时，新执行直接记为已读——否则角标会在用户眼皮底下
      // 跳出「1 new」。派生模式下「不算未读」的等价表达就是标已读
      if (viewingThisJob && payload.thread_id) {
        setReadRuns((r) => (r[payload.thread_id] ? r : { ...r, [payload.thread_id]: true }))
      }
      // 正在看该任务且窗口聚焦时不打扰；用户自己按的停止也不报「失败」。其余按通知开关弹系统通知
      if (
        notifyRef.current &&
        payload.status !== 'stopped' &&
        (!viewingThisJob || !document.hasFocus())
      ) {
        const t = tRef.current
        void window.lumi.notify?.({
          title: payload.status === 'success' ? t('notify.cronDone') : t('notify.cronFailed'),
          // 失败时 output 是「[failed] <错误全文>」——截 80 只剩个开头，系统通知
          // 本就按行数收，给足字数让错误至少露出关键那句（看全去执行记录）
          body: `${payload.job_name}: ${String(payload.output ?? '').slice(0, 200)}`,
        })
      }
      return
    }
    const sid = sessionKey(backend, ev.session_id ?? '')
    const parentRun: string = payload.parent_run_id ?? ''
    // 子代理的逐字流（正文/思考）不进 UI——只把子工具调用与 token 用量归属到父
    // agent 卡片（见下方 applyChildEvent）。中断类（审批/澄清/计划）即便带
    // parent_run_id 也照常往下走，仍需用户处理。
    if (parentRun && (type === 'message.delta' || type === 'message.start' || type === 'message.retry' || type === 'thinking.delta')) {
      return
    }
    // 系统通知：回复完成 + 等待用户处理的中断（审批/提问/计划）。
    // 仅在该会话非当前活动、或窗口未聚焦时弹（你正盯着时不打扰）。
    // 用 hasFocus 而非 document.hidden（切到别的应用时窗口仍可见，hidden 恒为 false）；
    // 通知经主进程发出（renderer 的 HTML5 Notification 在 macOS dev 下不可靠），
    // 点击由 onNotifyClick 回调切会话。
    if (notifyRef.current && (sid !== activeRef.current || !document.hasFocus())) {
      const t = tRef.current
      let title = ''
      let body = ''
      if (type === 'turn.complete') {
        title = t('notify.responseDone')
        // 取最后一条 user 消息（本轮触发的 prompt），而非 .find 拿到的会话首条
        const items = storeRef.current[sid]?.items ?? []
        const last = [...items].reverse().find((it) => it.kind === 'user')
        body = last && last.kind === 'user' ? last.text : ''
      } else if (type === 'approval.request') {
        title = t('approval.title')
        body = (payload.tool_calls ?? []).map((c: { name?: string }) => c.name).join(', ')
      } else if (type === 'clarify.request') {
        title = t('clarify.title')
        body = payload.questions?.[0]?.question ?? ''
      }
      if (title) {
        void window.lumi.notify?.({ title, body: String(body).slice(0, 80), tag: sid })
      }
    }
    // 事件 → 会话状态的纯归约（见 sessionReducer.reduceEvent）；此处只负责喂进去。
    setStore((store) => reduceEvent(store, sid, ev, defaultModelRef.current))
  }, [])

  // 为某会话建立一条独立 WS 连接（每会话一条，互不阻塞）。targetThread=null 为新会话。
  const openConnection = useCallback(
    (targetThread: string | null, targetWorkspace = '', backendId = 'local'): Promise<string> =>
      openChatConnection(targetThread, targetWorkspace, backendId, {
        getSession: key => storeRef.current[key],
        connsRef, activeRef, setStore, setConn, setWorkspaceDir, applySessionModel, handleEvent,
        onTasks: (backend, tasks) => setBgTasks(prev => replaceBackendTasks(prev, backend, tasks)),
      }),
    [handleEvent],
  )

  // 贴底状态的唯一写入点：pinnedRef 供 layout effect / handler 同步读，showJump 是其渲染镜像，
  // 二者恒为反相——集中到一处保证不漂移。稳定引用，可安全进 effect/callback 依赖。
  const setPinned = useCallback((v: boolean) => {
    pinnedRef.current = v
    setShowJump(!v)
  }, [])

  // 「贴底跟随」+「切会话归位」合一：用 layout effect（绘制前同步滚动，消除切会话/流式时
  // 内容先在旧位置闪一帧再跳底）。切会话先强制贴底并收起浮钮，随后只要贴底就跟到最新
  // （含 thinking 流，故依赖 thinkingText）。合一个 effect 也免去两 effect 读 pinnedRef 的顺序依赖。
  useLayoutEffect(() => {
    if (lastActiveRef.current !== active) {
      lastActiveRef.current = active
      setPinned(true)
    }
    const el = scrollRef.current
    const historyScroll = historyScrollRef.current
    if (el && historyScroll?.sid === active) {
      restoreHistoryScroll(el, historyScroll.anchor)
      historyScrollRef.current = null
      return
    }
    historyScrollRef.current = null
    if (el && pinnedRef.current) el.scrollTo({ top: el.scrollHeight })
  }, [active, items, running, approval, clarify, thinkingText, setPinned])

  // 滚动判定贴底，带滞回：距底 > 80px 才放手并浮出浮钮，回到 < 30px 才重新贴底并收起。
  // 中间留死区，避免用户停在边界附近时抖动反复 mount/卸载浮钮（光环涟漪重复闪）。
  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight
    if (dist > 80) setPinned(false)
    else if (dist < 30) setPinned(true)
    if (el.scrollTop < 120) loadOlderRef.current()
  }, [setPinned])

  const jumpToBottom = useCallback(() => {
    setPinned(true)
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [setPinned])

  useEffect(() => {
    if (conn !== 'open' || running) return
    // 别抢别处输入框的焦点：侧栏重命名 / 搜索正打字时别的会话回合结束，失焦会把半截
    // 标题提交出去
    const el = document.activeElement
    if (
      el instanceof HTMLInputElement ||
      (el instanceof HTMLTextAreaElement && el !== inputRef.current) ||
      (el instanceof HTMLElement && el.isContentEditable)
    )
      return
    inputRef.current?.focus()
  }, [conn, running, active])

  // 某台机器的控制连接（pin/重命名/删除/cron/项目等按所属机器路由）。缺位（离线 / 已删）
  // 返回 undefined，调用方 ?. 空操作——绝不回退到别的机器：同一 thread / 路径在另一台
  // 机器上是另一回事，请求会被静默落到错的地方。
  const gwForBackend = useCallback((backend: string) => controlConns.current[backend], [])

  // 后台任务属于当前会话的 bridge，必须发到该会话的连接（不是控制连接，
  // 否则后端按控制连接的 thread_id 匹配不到，停/清都是空操作、任务还会回来）。
  // 乐观更新只作用于当前会话所在机器的任务：task_id 可能跨机重名，按 active 的 backend 圈定
  const stopBgTask = useCallback((taskId: string) => {
    const be = keyBackend(activeRef.current)
    setBgTasks((ts) =>
      ts.map((x) => (x.task_id === taskId && beOf(x) === be ? { ...x, status: 'failed' as const } : x)),
    )
    void connsRef.current[activeRef.current]?.stopBgTask(taskId).catch(() => {})
  }, [])

  // 卡片输出预览：同样必须走本会话的连接（后端按 thread_id 校验归属）。
  // truncated/size 一并交给卡片——只回末 8KB，不标出来会被当成完整输出
  const readBgTaskOutput = useCallback(
    async (taskId: string): Promise<BgTaskOutput> =>
      (await connsRef.current[activeRef.current]?.readBgTaskOutput(taskId)) ?? EMPTY_BG_OUTPUT,
    [],
  )

  const dismissBgTask = useCallback((taskId: string) => {
    const be = keyBackend(activeRef.current)
    setBgTasks((ts) => ts.filter((x) => !(x.task_id === taskId && beOf(x) === be))) // 乐观移除
    void connsRef.current[activeRef.current]?.dismissBgTask(taskId).catch(() => {})
  }, [])

  const clearFinishedBgTasks = useCallback(() => {
    const be = keyBackend(activeRef.current)
    const tid = keyThread(activeRef.current)
    setBgTasks((ts) => ts.filter((x) => x.status === 'running' || !(x.thread_id === tid && beOf(x) === be)))
    void connsRef.current[activeRef.current]?.clearFinishedBgTasks().catch(() => {})
  }, [])

  // 当前会话的后台任务：一次 memo 派生，稳定引用避免 drawer 子树随每次 bg_tasks.update 重渲染。
  // bg 任务的 thread_id 是裸 thread，故按 active 的 thread + backend 双重过滤（同名飞书群跨机不串）。
  const activeBgTasks = useMemo(() => {
    const tid = keyThread(active)
    const be = keyBackend(active)
    return bgTasks.filter((tk) => tk.thread_id === tid && beOf(tk) === be)
  }, [bgTasks, active])
  const traditionalBgTasks = useMemo(() => activeBgTasks.filter(task => !task.subagent_id), [activeBgTasks])
  const runningBgCount = traditionalBgTasks.filter((tk) => tk.status === 'running').length
    + subagents.agents.filter(agent => agent.status !== 'idle').length
  const hasRunningBg = runningBgCount > 0
  const openSubagent = useCallback((id: string) => {
    subagents.select(id)
    dispatchPreview({ type: 'shrink' })
    if (!railOpen) toggleRail()
  }, [subagents.select, railOpen, toggleRail])
  const toggleSubagentExpanded = useCallback(() => {
    if (subagents.expanded && !railOpen) toggleRail()
    subagents.toggleExpanded()
  }, [subagents.expanded, subagents.toggleExpanded, railOpen, toggleRail])
  const stopSubagent = async (agent: import('./types').SubagentSummary): Promise<void> => {
    const result = await connsRef.current[active]?.stopSubagent(agent.agent_id)
    if (!result?.stopped) throw new Error(t('subagent.stopFailed'))
    subagents.refresh()
  }
  const isMacTitleBar = (window.lumi.platform ?? 'win32') === 'darwin'
  // 侧栏收起时也保留顶条：给浮钮组（展开/新对话）让出高度，避免盖住页面内容
  const showTopStrip = isMacTitleBar || !sidebarOpen

  // 跨机器 fan-out：对每台机器的控制连接各拉一次 list_sessions，打上机器标记后合并。
  // 某机器离线只跳过它，不影响其它机器（方案甲多机并存的合并列表）。
  // only：只刷某台机器（channel.activity 已知发生在哪台，不必打扰其它机器）。
  const refreshSessions = useCallback(async (only?: string) => {
    const conns = Object.entries(controlConns.current).filter(
      ([backend]) => !only || backend === only,
    )
    if (!conns.length) return
    const okBackends: string[] = []
    const perBackend = await Promise.all(
      conns.map(async ([backend, gw]) => {
        try {
          const r = await gw.listSessions()
          okBackends.push(backend)
          return r.sessions.map((s) => ({ ...s, backend }))
        } catch {
          // 该机器瞬时抖动（重连中）：保留它上一轮的会话，别整列抹掉导致闪没
          return sessionsRef.current.filter((s) => (s.backend || 'local') === backend)
        }
      }),
    )
    // 全量刷新整表替换（含回收已删机器的会话）；单机刷新只换该机器分段
    setSessions((prev) => {
      const next = only
        ? [...prev.filter((s) => beOf(s) !== only), ...perBackend.flat()]
        : perBackend.flat()
      // 后端还列不出的会话（新会话首轮 checkpoint 未落盘）：保留现有条目
      // （send 时的乐观插入），否则整表替换会让它从侧栏闪没、也无法切回。
      // 运行中之外也保住「当前活动」的：首条消息发送失败（running 已复位）时
      // 用户还在这个会话里，条目消失会让他切走后回不来；切走后自然回收。
      const keep = prev.filter((s) => {
        const key = sessionKey(beOf(s), s.thread_id)
        return (
          (storeRef.current[key]?.running || key === activeRef.current) &&
          !next.some((n) => n.thread_id === s.thread_id && beOf(n) === beOf(s))
        )
      })
      return [...keep, ...next]
    })
    // 只有真正成功的机器才算「已加载」（保持对象身份稳定，Sidebar 是 memo 的）
    setLoadedBackends((m) => {
      const fresh = okBackends.filter((b) => !m[b])
      if (!fresh.length) return m
      const next = { ...m }
      for (const b of fresh) next[b] = true
      return next
    })
  }, [])

  // 更新最新一页，保留已经加载的更早记录。
  const reloadHistory = useCallback(function reloadHistory(key: string, threadId: string): void {
    const revision = storeRef.current[key]?.historyRevision ?? 0
    connsRef.current[key]
      ?.loadHistory(threadId)
      .then((snapshot) => {
        setStore((store) => {
          const current = store[key]
          if (!current || (current.historyRevision ?? 0) !== revision) return store
          return { ...store, [key]: hydrateHistory(current, snapshot) }
        })
      })
      .catch(() => {})
  }, [])

  const loadOlderHistory = useCallback(async function loadOlderHistory(sid: string): Promise<void> {
    const state = storeRef.current[sid]
    const cursor = state?.historyCursor
    const gw = connsRef.current[sid]
    if (!cursor || !gw || historyRequests.current.has(sid)) return
    historyRequests.current.add(sid)
    setStore((store) => {
      const current = store[sid]
      if (!current) return store
      return { ...store, [sid]: { ...current, historyLoading: true, historyError: undefined } }
    })
    try {
      const page = await gw.loadHistory(keyThread(sid), cursor)
      if (storeRef.current[sid]?.historyCursor !== cursor || connsRef.current[sid] !== gw) return
      if (activeRef.current === sid && scrollRef.current) {
        historyScrollRef.current = { sid, anchor: captureHistoryScroll(scrollRef.current) }
        setPinned(false)
      }
      setStore((store) => {
        const current = store[sid]
        if (current?.historyCursor !== cursor) {
          if (historyScrollRef.current?.sid === sid) historyScrollRef.current = null
          return store
        }
        return { ...store, [sid]: prependHistory(current, page, cursor) }
      })
    } catch (error) {
      setStore((store) => {
        const current = store[sid]
        if (current?.historyCursor !== cursor) return store
        return { ...store, [sid]: { ...current, historyLoading: false, historyError: errorMessage(error) } }
      })
    } finally {
      historyRequests.current.delete(sid)
      const current = storeRef.current[sid]
      if (activeRef.current === sid && current?.historyCursor && !current.historyError
        && (current.historyCursor !== cursor || connsRef.current[sid] !== gw)
        && scrollRef.current && scrollRef.current.scrollTop < 120) {
        queueMicrotask(() => loadOlderRef.current())
      }
    }
  }, [setPinned])
  loadOlderRef.current = () => {
    const sid = activeRef.current
    if (!storeRef.current[sid]?.historyError) void loadOlderHistory(sid)
  }
  useEffect(() => {
    const el = scrollRef.current
    if (conn === 'open' && cur?.historyCursor && !cur.historyLoading && !cur.historyError && el && el.scrollTop < 120) {
      loadOlderRef.current()
    }
  }, [active, conn, cur?.historyCursor, cur?.historyLoading, cur?.historyError])

  // 各机器 IM 渠道快照（飞书组头状态灯 / 旁观横幅的审批模式与绑定项目）。
  // 各机器 ready 时只刷本机（only），设置面板关闭时全量（渠道配置在设置里改）；
  // 一次性合并写入，避免 N 台机器触发 N 次 Sidebar 重渲染。
  const [channels, setChannels] = useState<Record<string, ChannelInfo[]>>({})
  const refreshChannels = useCallback((only?: string) => {
    const conns = Object.entries(controlConns.current).filter(
      ([backend]) => !only || backend === only,
    )
    void Promise.all(
      conns.map(async ([backend, gw]) => {
        try {
          return [backend, (await gw.getChannels()).channels ?? []] as const
        } catch {
          return null
        }
      }),
    ).then((entries) => {
      const ok = entries.filter((e): e is readonly [string, ChannelInfo[]] => e !== null)
      if (ok.length) setChannels((c) => ({ ...c, ...Object.fromEntries(ok) }))
    })
  }, [])

  // 跨机器 fan-out 定时任务：每台机器各拉一次 list_cron_jobs，打机器标记后合并。
  // 传 only 时只刷该机器（cron.jobs 事件带来源机器 → 别把其他机器也白拉一遍），
  // 合并时保留其他机器上一轮的段。
  const refreshCronJobs = useCallback((only?: string) => {
    const conns = Object.entries(controlConns.current).filter(([b]) => !only || b === only)
    if (!conns.length) return
    void Promise.all(
      conns.map(async ([backend, gw]) => {
        try {
          const r = await gw.listCronJobs()
          return { jobs: r.jobs.map((j) => ({ ...j, backend })), ok: true }
        } catch {
          // 抖动：保留该机器上一轮的任务，别让它从列表消失
          return { jobs: cronJobsRef.current.filter((j) => beOf(j) === backend), ok: false }
        }
      }),
    ).then((results) => {
      const fetched = results.flatMap((r) => r.jobs)
      setCronJobs((prev) =>
        only ? [...prev.filter((j) => beOf(j) !== only), ...fetched] : fetched,
      )
    })
  }, [])
  useEffect(() => {
    refreshCronJobsRef.current = refreshCronJobs
  }, [refreshCronJobs])

  // 重连某机器：重新从配置取最新地址（设置里可能改过 SSH 配置）再 reconnect，
  // 否则 Gateway 仍持有构造时的旧地址。启用的机器其连接恒在 controlConns 里（失败态不删），
  // 走到无连接分支的只有已禁用/已删机器——这些不该建连（建连由 syncBackends 按 enabled 决定）。
  const reconnectMachine = useCallback(async (backend: string) => {
    const gw = controlConns.current[backend]
    if (!gw) return
    const connection = await window.lumi.getConnection(backend)
    gw.rebase(connection)
  }, [])

  // 乐观局部更新某会话字段（pin/重命名/自动标题共用）。按 thread + backend 精确匹配：
  // 飞书群在本地/远程 thread 同名，只按 thread 会把另一台机器的同名会话一起改了。
  const patchSession = useCallback(
    (tid: string, backend: string, patch: Partial<SessionMeta>) =>
      setSessions((prev) =>
        prev.map((s) => (s.thread_id === tid && beOf(s) === backend ? { ...s, ...patch } : s)),
      ),
    [],
  )

  // 机器断连/被移除：清掉它那份「进程级快照」状态（运行中的定时任务、后台任务）。
  // 这类状态只在连接活着时被推送更新，连接一断就再也等不到「结束」那一帧——留着会让
  // 任务永远显示运行中（定时任务的运行脉冲点还会顶掉未读角标）。重连后 gateway.ready
  // 各自重新拉一份，故清空是安全的。
  const clearMachineSnapshots = useCallback((backend: string) => {
    setCronRunning((r) => (r[backend]?.length ? { ...r, [backend]: [] } : r))
    setCronActiveRuns((r) => (r[backend]?.length ? { ...r, [backend]: [] } : r))
    setBgTasks((prev) =>
      prev.some((t) => beOf(t) === backend) ? replaceBackendTasks(prev, backend, []) : prev,
    )
  }, [])

  // 为某机器开控制连接（幂等）：ready 后拉它的会话 + 定时任务，连接态记入 machineConn。
  const openControlConn = useCallback(
    (backend: string): Promise<Gateway> => {
      return getControlConnection(backend, controlConns.current, controlOpening.current, async () => {
        const connection = await window.lumi.getConnection(backend)
        const gw = new Gateway(connection)
        gw.onEvent((ev) => {
          if (ev.type === 'gateway.ready') {
            // 只刷这台机器：每台 ready 都全量拉一遍，N 台机器启动就是 N² 次请求
            void refreshSessions(backend)
            refreshCronJobs(backend)
            refreshChannels(backend)
            // 后台任务初始快照：之后的变更经 bg_tasks.update 推送。没有这一拉，
            // 无会话连接的远程机器（其任务不由本端发起）任务面板会一直空着
            void gw
              .listBgTasks()
              .then((r) => setBgTasks((prev) => replaceBackendTasks(prev, backend, r.tasks)))
              .catch(() => {})
          }
          if (PROCESS_EVENTS.has(ev.type)) {
            handleEvent(ev, backend)
            return
          }
          // IM 渠道会话跑完一轮（进程级广播，仅在控制连接消费——每机器恰好一条，天然去重）：
          // 只刷本机会话列表；正在旁观该会话则重载历史（旁观视图无自己的流式事件）
          if (ev.type === 'channel.activity') {
            void refreshSessions(backend)
            const key = sessionKey(backend, ev.payload.thread_id)
            if (key === activeRef.current) reloadHistory(key, ev.payload.thread_id)
          }
          // 会话标题自动生成完成：就地更新该机器该会话的显示名，无需整表刷新。
          // 用户手动命名过的会话跳过——晚到的自动标题不得顶掉用户敲的名字。
          if (ev.type === 'session.title') {
            const { thread_id, title } = ev.payload
            if (!renamedRef.current.has(sessionKey(backend, thread_id))) {
              patchSession(thread_id, backend, { title })
            }
          }
        })
        gw.onState((st) => {
          setMachineConn((m) => (m[backend] === st ? m : { ...m, [backend]: st }))
          // 失败原因与状态同步取（gw 在通知前已记好），「连接」列表据此把该机器行的
          // 副标题换成人话；连上即为空串，红字自动消失。退避重试期间原因不变，
          // 同值直接返回原对象，省掉一轮无意义的下游重渲
          setMachineErr((m) => (m[backend] === gw.error ? m : { ...m, [backend]: gw.error }))
          if (st !== 'open') clearMachineSnapshots(backend)
        })
        controlConns.current[backend] = gw
        gw.connect()
        return gw
      }, reconnectMachine)
    },
    [refreshSessions, refreshCronJobs, refreshChannels, reloadHistory, reconnectMachine, patchSession, handleEvent, clearMachineSnapshots],
  )

  const ensureMachine = useCallback(async (backend: string) => {
    if (backend !== 'local') {
      const state = await window.lumi.backends?.list()
      const machine = state?.remotes.find(item => item.id === backend)
      if (machine?.enabled === false) {
        await window.lumi.backends?.save({ id: backend, enabled: true })
        window.dispatchEvent(new CustomEvent('lumi:backends-changed'))
      }
    }
    const gateway = await openControlConn(backend)
    await gateway.whenReady()
    return gateway
  }, [openControlConn])

  // 同步机器表 → 开新机器的控制连接、关掉已删机器的，刷新会话。BackendsPanel 增删后回调此。
  const syncBackends = useCallback(async () => {
    const data = await window.lumi.backends?.list()
    // 全量（含已禁用）入 machines：machineColor 按数组下标取色，删项会让其余机器串色
    const list = [
      { id: 'local', name: '本地', enabled: true },
      ...(data?.remotes ?? []).map((r) => ({ id: r.id, name: r.name || r.host, enabled: r.enabled !== false })),
    ]
    setMachines(list)
    // 仅对启用的机器开控制连接；禁用的（含本次刚关掉的）一并断开
    const wanted = new Set(list.filter((m) => m.enabled !== false).map((m) => m.id))
    for (const [id, gw] of Object.entries(controlConns.current)) {
      if (!wanted.has(id)) {
        gw.close()
        delete controlConns.current[id]
        // close() 不触发 onState，故连接态与各类快照都得在此手清，否则重新启用时会先
        // 闪一下旧的「已连接」、任务也会卡在旧的运行态上
        setMachineConn((m) => {
          const next = { ...m }
          delete next[id]
          return next
        })
        setMachineErr((m) => {
          const next = { ...m }
          delete next[id]
          return next
        })
        setChannels((c) => {
          const next = { ...c }
          delete next[id]
          return next
        })
        clearMachineSnapshots(id)
      }
    }
    // 该机器的会话连接一并关掉：否则仍连着已删 / 停用的机器，闪断重连、focus 唤醒照旧
    for (const [key, gw] of Object.entries(connsRef.current)) {
      if (!wanted.has(keyBackend(key))) {
        gw.close()
        delete connsRef.current[key]
      }
    }
    for (const id of wanted) void openControlConn(id).catch(() => {
      setMachineConn(current => ({ ...current, [id]: 'failed' }))
    })
    void refreshSessions()
  }, [openControlConn, refreshSessions, clearMachineSnapshots])

  // 查该机器登记的默认项目路径，顺带把 projects 同步成最新——boot effect
  // 与下方 goNewChat 共用同一份查找逻辑，避免各自倒腾一遍 listProjects。
  // 返回 null = 没查到（连接波动等），空串 = 查到了但确实没有默认项目，两者调用方处理不同
  // （前者该重试，后者不用）。声明在 boot effect 之前，纯是为了这条 useEffect 能引用它。
  const fetchDefaultProject = useCallback(
    async (backend: string): Promise<string | null> => {
      try {
        const r = await gwForBackend(backend)?.listProjects()
        if (!r) return null
        setProjects(r.projects)
        return r.projects.find((p) => p.default)?.path ?? ''
      } catch {
        return null
      }
    },
    [gwForBackend],
  )

  // 初始：起各机器控制连接（合并会话列表）；开局会话见下一条 effect。
  const bootedRef = useRef(false)
  useEffect(() => {
    void syncBackends()
    return () => {
      bootedRef.current = false
      Object.values(connsRef.current).forEach((g) => g.close())
      Object.values(controlConns.current).forEach((g) => g.close())
      // 必须清空 ref：close() 置 closedByUser=true 使其永不重连，若残留在 ref 里，
      // effect 重跑（HMR / 依赖变更）时 openControlConn 的幂等短路(if conns[id] return)
      // 会命中这些死连接而跳过重建 → 本地+远程控制连接全断、会话列表灰掉，只能整页重载。
      connsRef.current = {}
      controlConns.current = {}
    }
  }, [syncBackends])

  // A new chat is a target draft. Create its backend session only on send.
  const localConn = machineConn.local
  useEffect(() => {
    if (localConn !== 'open' || bootedRef.current) return
    bootedRef.current = true
    void fetchDefaultProject('local').then(workspace => {
      if (workspace && bootedRef.current) setNewTarget(current => current || { backend: 'local', path: workspace, name: basename(workspace) })
    })
  }, [localConn, fetchDefaultProject])

  // 兜底自愈：窗口重获焦点 / 网络恢复时，唤醒退避耗尽停摆的连接（它们不会自己再试，
  // 离开久了回来就一直离线），连上后各处挂在连接态上的取数自动重跑；焦点同时全量刷新
  // 会话列表（ready 首拉失败后没有别的重试路径，服务端有 checkpoint_id 缓存，很便宜）。
  useEffect(() => {
    const wake = () => {
      for (const gw of Object.values(controlConns.current)) gw.wake()
      for (const gw of Object.values(connsRef.current)) gw.wake()
    }
    const onFocus = () => {
      wake()
      void refreshSessions()
    }
    window.addEventListener('focus', onFocus)
    window.addEventListener('online', wake)
    return () => {
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('online', wake)
    }
  }, [refreshSessions])

  // BackendsPanel 增删/编辑远程机器后广播此事件 → 重连各机器、刷新合并列表（无 reload）。
  // detail.reconnectId：编辑了某机器的SSH 配置，需对该机器换址重连（syncBackends 幂等不会重建）。
  useEffect(() => {
    const onChanged = (e: Event) => {
      void syncBackends()
      const id = (e as CustomEvent<{ reconnectId?: string }>).detail?.reconnectId
      if (!id) return
      void reconnectMachine(id).catch(error => toast.error(errorMessage(error)))
      // 会话连接同样持有旧连接信息：换址重连，否则 1008 死在旧 token 上
      void window.lumi.getConnection(id).then(connection => {
        for (const [key, gw] of Object.entries(connsRef.current)) {
          if (keyBackend(key) === id) gw.rebase(connection)
        }
      }).catch(error => toast.error(errorMessage(error)))
    }
    window.addEventListener('lumi:backends-changed', onChanged)
    return () => window.removeEventListener('lumi:backends-changed', onChanged)
  }, [syncBackends, reconnectMachine])

  useEffect(() => window.lumi.ssh.onStatus(status => {
    if (status.phase === 'ready') {
      void window.lumi.getConnection(status.id).then(connection => {
        const control = controlConns.current[status.id]
        if (control?.state !== 'open') control?.rebase(connection)
        for (const [key, gateway] of Object.entries(connsRef.current)) {
          if (keyBackend(key) === status.id && gateway.state !== 'open') gateway.rebase(connection)
        }
      }).catch(() => {})
    }
    if (status.phase === 'offline') {
      setMachineConn(current => ({ ...current, [status.id]: 'closed' }))
    }
  }), [])

  // 按机器拉项目（方案甲先选机器）。「当前项目」高亮 = 活动会话的 workspaceDir
  // （由 activate/gateway.ready 维护），不再由后端下发。
  const refreshProjects = useCallback(
    async (backend = 'local') => {
      try {
        const r = await gwForBackend(backend)?.listProjects()
        if (r) setProjects(r.projects)
      } catch {
        /* 忽略：连接波动时静默 */
      }
    },
    [gwForBackend],
  )
  // 项目列表挂在所选机器的连接态上：连上 / 重连成功即重拉。开局抢在握手前、断线期间
  // 拉取失败都靠这里补上，不再定格成「还没有项目」
  const projectsConn = machineConn[projectsMachine]
  useEffect(() => {
    if (projectsConn === 'open') void refreshProjects(projectsMachine)
  }, [projectsConn, projectsMachine, refreshProjects])

  // 只在回合结束（running 落回 false）和切会话时刷新：发送时刷新没有新信息
  // （首条消息尚未落 checkpoint），白白多一次全量 checkpoint 扫描。
  useEffect(() => {
    if (active && !running) void refreshSessions(keyBackend(active))
  }, [active, running, refreshSessions])

  // 拉取斜杠命令（技能命令，按项目动态）。技能目录随项目变化，故进入命令模式时刷新。
  // 聊天页取活动会话的连接（命令在会话连接上执行）；项目主页经目标机器的控制连接按项目
  // 取——否则菜单/校验是别处的命令，发本项目独有的技能会被当普通文本。
  const loadCommands = useCallback(() => {
    const want = cmdTargetRef.current
    const req = homeTarget
      ? controlConns.current[homeTarget.backend]?.listCommands(homeTarget.path)
      : connsRef.current[activeRef.current]?.listCommands()
    req
      ?.then((r) => {
        if (cmdTargetRef.current === want) setCommands(r.commands ?? [])
      })
      .catch(() => {})
  }, [homeTarget, cmdTargetRef])

  // provider 列表响应统一回写。active = 新会话默认，只影响之后新建的会话——顶部选择器
  // 读的是 store[key].model（本会话的），不在这里改
  const applyProviderResp = useCallback(
    (r: { profiles?: ProviderProfile[]; active?: ActiveModel; classifier?: ModelPointer }) => {
      setProviders(r.profiles ?? [])
      setDefaultModel(r.active ?? { provider: '', model: '' })
      setClassifier(r.classifier ?? {})
    },
    [],
  )

  // 把后端下发的会话模型落到该会话（switch_session / set_session_model 共用）；
  // 「只存已固化的」这条语义归 sessionModelPatch（见 sessionReducer）。
  const applySessionModel = useCallback((key: string, m: SessionModelWire) => {
    setStore((s) => (s[key] ? { ...s, [key]: { ...s[key], ...sessionModelPatch(m) } } : s))
  }, [])

  // providers 是机器级配置：走模型菜单所指机器的控制连接
  const loadProviders = useCallback(
    (backend: string) => {
      controlConns.current[backend]
        ?.listProviders()
        .then((r) => {
          if (pickerRef.current === backend) applyProviderResp(r)
        })
        .catch(() => {})
    },
    [applyProviderResp, pickerRef],
  )

  // 模型菜单换了机器、或该机器连上（首次点开未建连的远程会话时请求会落空）即重载
  const pickerConn = machineConn[pickerBackend]
  useEffect(() => {
    if (pickerConn === 'open') loadProviders(pickerBackend)
  }, [pickerBackend, pickerConn, loadProviders])

  // 档位按 (连接, 模型) 存，对所有用该模型的会话生效——按的是选择器当前指着的那个模型
  const switchEffort = (level: string, target: ActiveModel) => {
    const backend = pickerBackend
    gwForBackend(backend)
      ?.setEffort(target.provider, target.model, level)
      .catch((e) => console.error('set_effort 失败:', e))
      .finally(() => loadProviders(backend))
  }

  // 切模型：只切**本会话**（下一轮生效），别的会话与「新会话默认」都不动。
  // key 在开弹窗时就捕获：确认期间用户可能已切走会话，那时的 activeRef 是别人
  const applySwitchModel = (provider: string, model: string, key: string) => {
    connsRef.current[key]
      ?.setSessionModel(provider, model)
      .then((r) => applySessionModel(key, r))
      .catch((e) => console.error('set_session_model 失败:', e))
  }
  // 项目主页没有「本会话」可切（active 还是上一个会话，改它是张冠李戴，同模型 /
  // ApprovalModePicker 的 !project 取舍）：那里选模型的意思是「我要开的新会话用它」，
  // 故改「新会话默认」。会话内切换直接生效，不弹确认（换模型可逆，不值得阻断）。
  const switchModel = (provider: string, model: string, project: boolean) => {
    if (project) {
      gwForBackend(projectHome?.backend ?? 'local')
        ?.setProvider(provider, model)
        .then((r) => setDefaultModel(r.active))
        .catch((e) => console.error('set_provider 失败:', e))
      return
    }
    applySwitchModel(provider, model, activeRef.current)
  }

  // 设置面板改了某机器的 provider 后回调：若改的正是模型菜单所指机器，刷新之
  const onProvidersChanged = useCallback(
    (machine: string) => {
      if (machine === pickerRef.current) loadProviders(machine)
    },
    [loadProviders, pickerRef],
  )

  // 激活一个会话：无现成连接时先建立（target=null 为新会话），并同步连接指示灯。
  // connect→setActive→setConn 的握手只写在这一处，五个入口共用。
  const activate = useCallback(
    async (target: string | null, workspace = '', backend = 'local') => {
      // key = 会话前端身份（backend+thread）；target 为裸 thread（新会话时 null）
      let key = target ? sessionKey(backend, target) : ''
      // 乐观切换：握手/重试需要时间，先把 UI 落到目标会话（空内容 + 黄灯示意），
      // 避免 view 已切、active 未动的脱节（如 cron 视图残留渲染成普通聊天）。
      // 晚到的建连结果经 seq 判废弃：用户已切走时不回拽视图。
      const seq = ++activationSeqRef.current
      if (key) {
        setActive(key)
        closeAllPreviews()
      }
      // 死连接驱逐：failed / 主动关闭的连接不再自愈，占着 key 会让复用分支永远拿到死连接
      if (key && connsRef.current[key]?.dead) delete connsRef.current[key]
      if (!key || !connsRef.current[key]) {
        // 建立期间以 connecting 示意（sidecar 不可用时指示灯保持黄色而非静默无反应）
        setConn('connecting')
        key = await openConnection(target, workspace, backend)
        if (activationSeqRef.current !== seq) return key
      } else {
        if (workspace) {
          // 已连会话：进程 cwd 可能被别的会话改过，重发带 workspace 的 switch 切回本会话项目
          void connsRef.current[key].switchSession(target!, workspace)
        }
        // 渠道会话没有自己的流式事件，后台期间的轮次不会写入缓存 store；
        // channel.activity 又只在「正在旁观」时重载——切回时必须重拉历史，否则永远是旧账。
        // store 为空也重拉（自愈）：初次加载若被后端悬挂吞掉（无 reject、连接未断，
        // loaded 补拉不会触发），旧逻辑下再点多少次都停在空白，只能重载应用
        const meta = sessionsRef.current.find(
          (s) => s.thread_id === target && beOf(s) === backend,
        )
        if (meta?.channel || !storeRef.current[key]?.items.length) reloadHistory(key, target!)
      }
      // 无项目（cron 执行会话等）写空串：别让上个会话的项目留在指示上
      setWorkspaceDir(workspace)
      setActive(key) // activeBackend 从 active 派生，无需单独设
      // 按连接真实状态点灯：openConnection 失败路径也会 resolve（乐观切换契约），
      // 硬编码 'open' 会在连接实际已断时点亮假绿灯
      setConn(connsRef.current[key]?.state ?? 'open')
      closeAllPreviews() // 切会话关掉预览，避免上个会话的文件残留
      return key
    },
    [openConnection, reloadHistory, closeAllPreviews],
  )

  const openProjects = useCallback(() => {
    setNeedProjectHint(false) // 用户主动点「项目」标签，不是被新建会话逼过来的，不提示
    setView('projects')
    void refreshProjects(projectsMachine)
  }, [refreshProjects, projectsMachine])

  // 项目视图切机器（先选机器）。换机器先清成未加载（别把上一台的列表挂在这台名下），
  // 由连接态 effect 拉取；点的仍是当前机器则就地重拉
  const selectProjectsMachine = useCallback(
    (machine: string) => {
      if (machine === projectsMachine) return void refreshProjects(machine)
      setProjects(null)
      setProjectsMachine(machine)
    },
    [projectsMachine, refreshProjects],
  )

  // 在指定机器与工作目录开新会话；不登记项目时也显式传入所选机器的主目录。
  const newSession = useCallback(
    async (backend = 'local', workspace = '') => {
      setView('chat')
      const key = await activate(null, workspace, backend)
      void refreshSessions()
      return key
    },
    [activate, refreshSessions],
  )

  // 指定机器「新建会话」：有登记过的默认项目就直接进，否则阻断去选择器。每次都问
  // 后端要最新列表（而非信任本地缓存的 projects state）——default 标记可能刚在另一
  // 台设备/另一个窗口改过。
  const goNewChat = useCallback(async (backend: string) => {
    const revision = ++newTargetRevision.current
    setView('newchat')
    setActive('')
    try {
      const gateway = await ensureMachine(backend)
      const result = await gateway.listProjects()
      const project = result.projects.find(item => item.default) || result.projects[0]
      if (newTargetRevision.current === revision) setNewTarget(project ? { backend, path: project.path, name: project.name } : { backend, path: '', name: t('picker.choose') })
    } catch (error) { toast.error(errorMessage(error)) }
  }, [ensureMachine, t])
  // 稳定引用，让 memo 化的 AppTitleBar 在流式 token 重渲染时不陪跑。
  const startNewChat = useCallback(() => void goNewChat(activeBackend), [activeBackend, goNewChat])

  useEffect(() => {
    return window.lumi.onMenuAction?.((action) => {
      if (action === 'new-chat') startNewChat()
      if (action === 'settings') openSettings()
    })
  }, [startNewChat, openSettings])

  const selectSession = useCallback(
    async (tid: string, backend = 'local') => {
      setView('chat')
      // 按 thread + backend 精确定位：飞书群在本地/远程 thread 同名，只按 thread 会选错机器
      if (sessionKey(backend, tid) !== activeRef.current) {
        const s = sessionsRef.current.find((x) => x.thread_id === tid && beOf(x) === backend)
        await activate(tid, s?.workspace_dir || '', backend)
      }
    },
    [activate],
  )

  // 通知点击：主进程已聚焦窗口，这里切到对应会话。走 selectSession 而非裸 activate：
  // 停在定时 / 项目页时要切回聊天页，且带上该会话的项目
  useEffect(
    () =>
      window.lumi.onNotifyClick?.((tag) => {
        if (tag) void selectSession(keyThread(tag), keyBackend(tag) || 'local')
      }),
    [selectSession],
  )

  // 定时页按机器管理：侧栏入口落本机；项目主页跳过去落该项目所在机器，点某条任务直达
  // 其详情，「新建任务」直接打开绑定本项目的创建表单
  const [cronEntry, setCronEntry] = useState<{ machine: string; jobId?: string; createIn?: string }>({
    machine: 'local',
  })
  const openScheduled = useCallback(() => {
    setCronEntry({ machine: 'local' })
    setView('scheduled')
  }, [])
  const openHomeScheduled = useCallback(
    (target?: { jobId?: string; create?: boolean }) => {
      setCronEntry({
        machine: projectHome?.backend ?? 'local',
        jobId: target?.jobId,
        createIn: target?.create ? projectHome?.path : undefined,
      })
      setView('scheduled')
    },
    [projectHome],
  )

  // 打开项目 = 在该机器开一条绑定到此项目的新会话（项目经 open 握手随会话绑定，
  // 不再先在共享连接上 setWorkspace 改进程态——那对新会话的独立连接无效）
  const openProject = useCallback(
    async (path: string, backend = 'local') => {
      try {
        await newSession(backend, path)
      } catch {
        /* 忽略：连接波动时静默 */
      }
    },
    [newSession],
  )

  // 项目主页：点项目卡片进入落地页（会话流 + 提示词/记忆/定时/技能/Agent 五卡）
  const openProjectHome = useCallback((path: string, backend = 'local') => {
    setProjectHome({ backend, path })
    setView('project')
    // 输入栏在项目页与聊天页共用同一份 input/attachments：进项目页先清空，
    // 免得上个会话的草稿/附件串到「在此项目开新会话」里
    setInput('')
    setAttachments([])
  }, [])

  // 项目主页输入岛：复用主输入栏（input/attachments/斜杠命令全共用），发送即在此项目
  // 新建会话并携带首条消息。新会话此刻还没进 active，override 自带目标与附件，规避渲染时序依赖。
  const startProjectChat = useCallback(async () => {
    const target = homeTarget
    if (!target?.path) { toast.info(t('ssh.selectProject')); return }
    const text = input.trim()
    const atts = attachments
    if (!text && atts.length === 0) return
    // 机器已知离线时直接提示，保留草稿供恢复连接后重试。
    if (controlConns.current[target.backend]?.state !== 'open') {
      toast.error(t('projhome.offline'))
      return
    }
    let key: string
    try { key = await newSession(target.backend, target.path) } catch (error) { setView(view === 'newchat' ? 'newchat' : 'project'); toast.error(errorMessage(error)); return }
    sendRef.current({ text, key, workspace: target.path, atts })
    // 清空放在发送派发之后：建会话若中途失败/悬挂，输入仍留在框里可重试，不丢字。
    setInput('')
    setAttachments([])
  }, [homeTarget, newSession, input, attachments, t, view])

  // 项目主页专用 API：只认目标机器的控制连接，缺位返回 undefined（同 gwForBackend）。
  // useCallback 稳定引用：ProjectHomePage 的加载 effect 依赖它，不稳会随 App 重渲染风暴重发
  const projectHomeApi = useCallback(
    () => (projectHome ? controlConns.current[projectHome.backend] : undefined),
    [projectHome],
  )
  // 项目主页 props 全部稳定引用：App 是流式事件的重渲染热点（后台会话每个 token 都
  // 触发 setStore），配合 ProjectHomePage 的 memo()，停在项目页时不再整页 reconcile
  // 当前项目的登记条目（name/default 都从这一次 find 取）
  const homeProject = projectHome
    ? projects?.find((p) => p.path === projectHome.path)
    : undefined
  const homeProjectInfo = useMemo(
    () =>
      projectHome
        ? { name: homeProject?.name ?? basename(projectHome.path), path: projectHome.path }
        : null,
    [projectHome, homeProject?.name],
  )
  const homeSessions = useMemo(
    () =>
      projectHome
        ? sessions.filter(
            (s) =>
              beOf(s) === projectHome.backend &&
              s.workspace_dir === projectHome.path &&
              !s.channel,
          )
        : [],
    [sessions, projectHome],
  )
  const homeCronJobs = useMemo(
    () => (projectHome ? cronJobs.filter((j) => beOf(j) === projectHome.backend) : []),
    [cronJobs, projectHome],
  )
  const openHomeSession = useCallback(
    (tid: string) => void selectSession(tid, projectHome?.backend ?? 'local'),
    [selectSession, projectHome],
  )
  const toggleHomeCron = useCallback(
    (id: string, enabled: boolean) => {
      const backend = projectHome?.backend ?? 'local'
      void gwForBackend(backend)
        ?.toggleCronJob(id, enabled)
        .then(() => refreshCronJobs(backend))
        .catch(() => {})
    },
    [gwForBackend, projectHome, refreshCronJobs],
  )

  // 新建项目：在该机器登记（带名）→ 进入该项目
  const createProject = useCallback(
    async (path: string, name: string, backend = 'local') => {
      setShowNewProject(false)
      try {
        const r = await gwForBackend(backend)?.addProject(path, name)
        if (r) setProjects(r.projects)
        await openProject(path, backend)
      } catch {
        /* 目录不可用等：保持页面现状 */
      }
    },
    [gwForBackend, openProject],
  )

  // 只删书签列表条目：移除的项目若正是当前会话绑定的目录，该会话本身仍在正常工作
  // （移除书签不解绑会话），workspaceDir 应继续如实反映它，不能清空——之前在这里
  // 清空过，结果 workspaceDirRef 和后端仍在上报的 mcp.status.project 对不上，
  // 该会话真实的 MCP 故障 toast 被误判为"跨项目噪音"而静默吞掉。
  const removeProjectFromList = useCallback(
    (path: string, backend = 'local') => {
      gwForBackend(backend)?.removeProject(path).then((r) => setProjects(r.projects)).catch(() => {})
    },
    [gwForBackend],
  )

  const renameProjectInList = useCallback(
    (path: string, name: string, backend = 'local') => {
      gwForBackend(backend)?.renameProject(path, name).then((r) => setProjects(r.projects)).catch(() => {})
    },
    [gwForBackend],
  )

  const setProjectDefault = useCallback(
    (path: string, isDefault: boolean, backend = 'local') => {
      gwForBackend(backend)
        ?.setDefaultProject(path, isDefault)
        .then((r) => setProjects(r.projects))
        .catch(() => {})
    },
    [gwForBackend],
  )

  // 在任务会话视图内切换到某次执行的会话（不改变 view），并标记该次执行为已读。
  // 已读集合封顶 500 条（对象按插入序，砍最旧的），避免 localStorage 无限增长。
  // 定时任务所属机器（操作/执行会话都路由到它）
  const cronBackendOf = useCallback(
    (jobId: string) => cronJobsRef.current.find((j) => j.id === jobId)?.backend || 'local',
    [],
  )
  // RunsSection 的 api 必须稳定引用（仅随当前任务变化）：内联箭头会让 useCronRuns 在主对话
  // 流式期间每个 token 都重拉 list_cron_runs。
  const runsRailApi = useCallback(
    () => gwForBackend(cronBackendOf(activeCronJob ?? '')),
    [gwForBackend, cronBackendOf, activeCronJob],
  )
  const openRunThread = useCallback(
    async (tid: string, backend = 'local') => {
      setCronRunThread(tid)
      setReadRuns((r) => {
        if (r[tid]) return r
        const next = { ...r, [tid]: true as const }
        // 封顶 5000：它是未读的唯一事实源，裁早了老 run 会回潮成未读。每任务
        // run_threads 窗口 50，5000 够 100 个任务全部已读仍不越界（约 250KB）
        const keys = Object.keys(next)
        for (const k of keys.slice(0, Math.max(0, keys.length - 5000))) delete next[k]
        return next
      })
      await activate(tid, '', backend)
    },
    [activate],
  )

  // 打开某任务的会话视图：默认选中最近一次有会话的执行（在任务所属机器上查/开）
  const openCronJob = useCallback(
    async (jobId: string, threadId?: string) => {
      setView('cronjob')
      setActiveCronJob(jobId)
      // auto-open 最近一次有会话的执行：run_threads 已是倒序的可跳转 run，取头即可
      // （RunsSection 自己拉完整列表，这里不必再走一趟 listCronRuns）。
      // 未读随「点开即标已读」自然消：这条经 openRunThread 标记，其余留待逐条点开
      const job = cronJobsRef.current.find((j) => j.id === jobId)
      const tid = threadId ?? job?.run_threads?.[0]
      setCronRunThread(tid ?? null)
      if (tid) await openRunThread(tid, beOf(job ?? {}))
    },
    [openRunThread],
  )

  // 会话标记（pin/重命名/删除）存在各机器自己的 ~/.lumi，故按会话所属机器路由（backend 由行透传）
  const pinSession = useCallback(
    (tid: string, backend: string, pinned: boolean) => {
      // 乐观更新（Sidebar 按 pinned 重新分组）；成功后再断言一次，纠正 RPC 在途时
      // 并发刷新读到旧值的回退；失败则全量刷新回滚
      patchSession(tid, backend, { pinned })
      gwForBackend(backend)
        ?.pinSession(tid, pinned)
        .then(() => patchSession(tid, backend, { pinned }))
        .catch(() => { void refreshSessions() })
    },
    [gwForBackend, refreshSessions, patchSession],
  )

  const renameSession = useCallback(
    (tid: string, backend: string, title: string) => {
      // 乐观更新；成功后再断言一次，纠正并发刷新回退；失败则刷新回滚。
      // 手动命名标记（清空命名即撤销）挡住晚到的 session.title 自动标题广播。
      const key = sessionKey(backend, tid)
      if (title) renamedRef.current.add(key)
      else renamedRef.current.delete(key)
      patchSession(tid, backend, { title })
      gwForBackend(backend)
        ?.renameSession(tid, title)
        .then(() => patchSession(tid, backend, { title }))
        .catch(() => {
          renamedRef.current.delete(key) // 后端未写入，标记随之回滚
          void refreshSessions()
        })
    },
    [gwForBackend, refreshSessions, patchSession],
  )

  const deleteSession = async (session: SessionMeta) => {
    setPendingDelete(null)
    const tid = session.thread_id
    const backend = beOf(session)
    const key = sessionKey(backend, tid)
    // 乐观更新：立即从列表移除（按 thread + backend，避免连带删掉另一台机器的同名会话）
    const removeRow = () =>
      setSessions((prev) => prev.filter((s) => !(s.thread_id === tid && beOf(s) === backend)))
    removeRow()
    try {
      // 等后端删除提交后再清理本地 / 切会话——否则 goNewChat 触发的刷新
      // 会读到尚未删除的 checkpoint，把会话又加回列表
      await gwForBackend(backend)?.deleteSession(tid)
    } catch {
      void refreshSessions() // 删除失败：把会话找回来（此时本地连接 / 缓存尚未清理）
      return
    }
    removeRow() // 再断言一次，纠正删除期间并发刷新读回的行
    connsRef.current[key]?.close()
    delete connsRef.current[key]
    setStore((s) => {
      if (!(key in s)) return s
      const n = { ...s }
      delete n[key]
      return n
    })
    // 删除的是当前会话：另开一个新会话顶上。走 goNewChat 而非裸 activate(null)——
    // 后者不带 workspace，后端会以「请先选择项目」拒收首条消息
    if (key === activeRef.current) await goNewChat(backend)
  }

  // 读取图片文件为 data URL 加入附件（粘贴 / 拖拽 / ＋ 选择 共用，仅图片类型）
  // 图片读成 data URL 嵌入；其它文件取绝对路径作引用（拿不到路径则跳过——非 Electron 环境）
  const addFiles = (files: FileList | File[]) => {
    const failed: string[] = []
    for (const f of Array.from(files)) {
      if (f.type.startsWith('image/')) {
        const reader = new FileReader()
        reader.onload = () => {
          if (typeof reader.result === 'string') {
            const url = reader.result
            setAttachments((a) => [...a, { id: nid(), kind: 'image', dataUrl: url, name: f.name, blob: f }])
          }
        }
        reader.readAsDataURL(f)
        continue
      }
      const path = window.lumi.getPathForFile?.(f) || ''
      if (path)
        setAttachments((a) => [...a, { id: nid(), kind: 'file', path, name: f.name, blob: f }])
      else failed.push(f.name) // 取不到绝对路径（如非文件系统来源的拖拽），别静默吞掉
    }
    if (failed.length) {
      toast.error(`${t('composer.attachFailed')}: ${failed.join('、')}`)
    }
  }

  const onPasteImages = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items
    if (!items) return
    const files: File[] = []
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/')) {
        const f = items[i].getAsFile()
        if (f) files.push(f)
      }
    }
    if (files.length) {
      e.preventDefault() // 阻止把图片当文件名/文本贴入
      addFiles(files)
    }
  }

  const onDropFiles = (e: React.DragEvent) => {
    if (e.dataTransfer?.files?.length) {
      e.preventDefault()
      addFiles(e.dataTransfer.files)
    }
  }

  const removeAttachment = (id: number) => setAttachments((a) => a.filter((x) => x.id !== id))

  // 流式 RPC 被 reject（连接断开时 gateway 会 flush 所有在飞请求）后，
  // 新连接不会为死掉的 run 补发 turn.complete——必须在此复位 running，
  // 否则该会话永久卡死（输入框禁用、stop 无效）。
  const resetRunning = (sid: string) =>
    setStore((s) => (s[sid] ? { ...s, [sid]: { ...s[sid], running: false } } : s))

  // send/runCommand/时间旅行的统一失败兜底：复位 running + 亮出后端拒绝原因 +
  // 重载历史对齐后端真相——被拒的乐观改动（幽灵气泡 / 提前截断）随整表替换消失
  const reportSendFailure = (sid: string, err: unknown) => {
    resetRunning(sid)
    toastRejection(err)
    reloadHistory(sid, keyThread(sid))
  }

  // 亮出后端拒绝原因（无 message 的错误静默）
  const toastRejection = (err: unknown) => {
    const message = err && typeof err === 'object' && 'message' in err ? String(err.message) : ''
    if (message) toast.error(message)
  }

  // 撤出托盘里的待插入消息（入箱失败 / 撤回 / 停止退回）
  const dropFromTray = (sid: string, messageIds: string[]) =>
    setStore((s) => (s[sid] ? { ...s, [sid]: dropPending(s[sid], messageIds) } : s))

  // 开轮：置 running，并可选地乐观插入用户气泡（系统命令轮传 null——它们不落库
  // 为用户消息，插了会在回合结束刷新时当着用户的面消失）
  const startTurn = (sid: string, bubble: Item | null) =>
    setStore((s) => ({
      ...s,
      [sid]: {
        ...s[sid],
        items: bubble ? [...s[sid].items, bubble] : s[sid].items,
        running: true,
        runStart: Date.now(),
      },
    }))

  // override：项目主页输入岛用——显式指定文本/附件与目标会话（新建的会话此刻还没
  // 进 active），绕开「等 React 把新 state 渲染进闭包」的时序依赖。附件随 override 显式
  // 传入（调用方发送后自行清空），不再默默丢弃。
  type SendOverride = { text: string; key: string; workspace: string; atts: Attachment[] }
  const sendRef = useRef<(o?: SendOverride) => void>(() => {})
  const send = (o?: SendOverride) => {
    const sid = o?.key ?? active
    const text = (o ? o.text : input).trim()
    const atts = o ? o.atts : attachments
    const imgs = atts.filter((a) => a.kind === 'image')
    const fileRefs = atts.filter((a) => a.kind === 'file')
    const attCount = imgs.length + fileRefs.length
    const gw = connsRef.current[sid]
    // 两种入口等价：非 override 时 sid=active，而 running 本就是 store[active]?.running
    // 运行中发送 = 插话：进托盘，等后端在下一个工具边界注入（见 sendMessage 注释）
    const busy = storeRef.current[sid]?.running ?? false
    if ((!text && attCount === 0) || !gw) return
    // 纯文本的已知斜杠命令走 run_command；带附件则一律走 send_message。
    const [cmdName, cmdExtra] = attCount === 0 && text.startsWith('/') ? parseCommand(text) : ['', '']
    const cmd = cmdName ? commands.find((c) => c.name === cmdName) : undefined
    if (cmd && busy) {
      // 命令要单独开轮，插不进运行中的轮：留在输入框，等这轮结束再发
      toast.info(t('composer.commandBusy'))
      return
    }
    // 主动发送即视为「回到对话」：强制贴底，确保自己的消息与随后的回复都在视野内
    setPinned(true)
    if (!o) {
      setInput('')
      setAttachments([])
    }
    const files: AttachedFile[] = fileRefs.map((a) => ({ path: a.path, name: a.name }))
    const bubble = userBubble(
      text, // 可见正文只留用户输入；附件路径走 system-reminder，不污染气泡
      imgs.length ? imgs.map((a) => a.dataUrl) : undefined,
      files.length ? files : undefined,
    )
    // 此分支必须在乐观插入之前分流：/compact、/dream 这类系统命令不产生 checkpoint
    // 条目，不该插气泡（后端永远列不出，回合结束刷新时会当着用户的面消失；幽灵
    // 气泡还会破坏本地与后端 user 消息的对应关系）；技能命令会落库并声明
    // 「/名 输入」气泡，照常乐观插入（其 id 同样由 turn.start 上锚）。
    if (cmd) {
      startTurn(sid, cmd.type === 'system' ? null : bubble)
      gw.runCommand(cmdName, cmdExtra, toolMode).catch((err) => reportSendFailure(sid, err))
      return
    }
    // 插话 id 前端预生成：注入事件可能先于 RPC 应答到达，托盘须事先认得出自己的消息
    const interjectId = busy ? crypto.randomUUID() : undefined
    if (interjectId) {
      setStore((s) =>
        s[sid] ? { ...s, [sid]: addPending(s[sid], { ...bubble, messageId: interjectId }) } : s,
      )
    } else {
      startTurn(sid, bubble)
    }
    // 新会话首条消息：checkpoint 未落盘前后端列不出来，先乐观插入侧栏条目——
    // 否则首轮期间会话在侧栏缺席、切出去就回不来。回合结束的整表刷新会以
    // 后端真实数据替换（display_time 沿用后端的相对时间文案格式）。
    const tid = keyThread(sid)
    const be = keyBackend(sid) || 'local'
    if (!sessionsRef.current.some((s) => s.thread_id === tid && beOf(s) === be)) {
      setSessions((prev) => [
        {
          thread_id: tid,
          // 摘要与后端一致，避免回复完成后标题跳变。
          first_message: [...text].slice(0, 100).join(''),
          title: '',
          pinned: false,
          created_at: new Date().toISOString(),
          message_count: 1,
          display_time: 'just now',
          workspace_dir: o?.workspace ?? workspaceDir,
          backend: be,
        },
        ...prev,
      ])
    }
    dispatchDraft(gw, { text, attachments: atts }, { remote: be !== 'local', toolMode, messageId: interjectId }, (err) => {
      // 首条消息的 override 同样需要恢复：上传失败时后端尚未收到正文或附件。
      setInput(text)
      setAttachments(atts)
      if (interjectId) {
        // 插话入箱失败（满额 / 断连）：只撤托盘，不动仍在跑的轮
        dropFromTray(sid, [interjectId])
        toastRejection(err)
        return
      }
      reportSendFailure(sid, err)
    })
  }
  sendRef.current = send // 供项目主页输入岛在 newSession 后调用（override 自带目标，无时序依赖）

  // ── 会话分支 ──
  const [forking, setForking] = useState<string | null>(null)
  const forkBusyRef = useRef(false)
  const forkRequestsRef = useRef(new Map<string, string>())
  async function forkChat(point: ForkPoint): Promise<void> {
    const sourceKey = activeRef.current
    const gateway = connsRef.current[sourceKey]
    if (!gateway || forkBusyRef.current) return
    const requestKey = `${sourceKey}:${point.checkpoint_id}:${point.message_id}`
    const requestId = forkRequestsRef.current.get(requestKey) ?? crypto.randomUUID()
    forkRequestsRef.current.set(requestKey, requestId)
    forkBusyRef.current = true
    setForking(requestKey)
    const activationSeq = activationSeqRef.current
    const backend = keyBackend(sourceKey)
    try {
      const result = await gateway.forkSession(
        keyThread(sourceKey), point.checkpoint_id, point.message_id, requestId,
      )
      forkRequestsRef.current.delete(requestKey)
      await refreshSessions(backend)
      // 请求期间用户可能已切走；新会话保留在侧栏，不把用户拉回。
      if (activeRef.current === sourceKey && activationSeqRef.current === activationSeq) {
        setView('chat')
        await activate(result.thread_id, result.workspace_dir, backend)
      }
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      forkBusyRef.current = false
      setForking(null)
    }
  }

  // ── 时间旅行（重新生成 / 编辑重发）──
  // 以目标用户气泡为锚截断其后历史（后端同步删 checkpoint 消息），再以
  // 原文（重答）或编辑后文本重走一轮。锚点只认 messageId——由 turn.start 上锚或
  // load_history 下发，不做序号/文本猜测：本地列表可能含后端没有的条目（发送失败
  // 残留等）。id 缺失或气泡已不在（历史被整理）即刷新对齐后端，不冒险截错。
  // newText 为空 = 重新生成（保留原气泡），非空 = 编辑重发（换成新气泡）。
  const timeTravel = (itemId: Item['id'], newText?: string) => {
    const sid = active
    const gw = connsRef.current[sid]
    const st = storeRef.current[sid]
    if (!gw || st?.running) return
    setEditingId(null)
    const idx = st?.items.findIndex((it) => it.id === itemId) ?? -1
    const anchor = idx >= 0 ? st!.items[idx] : undefined
    if (anchor?.kind !== 'user' || !anchor.messageId) {
      reloadHistory(sid, keyThread(sid))
      return
    }
    const mid = anchor.messageId
    setPinned(true)
    // 截断 + 重挂气泡一次写入：重答沿用原气泡（后端重建同一句话），编辑换成新气泡
    // （附件展示沿用原条目，后端按原消息声明重挂）。两者都清掉 messageId——后端截断
    // 后以新 id 重挂，留着旧 id 会让下一次时间旅行指向已删除的消息；新 id 由本轮
    // turn.start 重新上锚。
    setStore((s) => ({
      ...s,
      [sid]: {
        ...s[sid],
        items: [
          ...s[sid].items.slice(0, idx),
          newText === undefined
            ? { ...anchor, messageId: undefined }
            : userBubble(newText, anchor.images, anchor.files),
        ],
        running: true,
        runStart: Date.now(),
        historyCursor: null,
        historyReset: true,
        historyRevision: (s[sid].historyRevision ?? 0) + 1,
        historyLoading: false,
      },
    }))
    const rpc =
      newText === undefined
        ? gw.regenerate(mid, toolMode)
        : gw.editResend(mid, newText, toolMode)
    rpc.catch((err) => {
      reportSendFailure(sid, err)
      reloadHistory(sid, keyThread(sid))
    })
  }

  // 中止当前流式轮：后端取消 task 并补发 turn.complete，running 随之复位。
  // 未注入的插话由后端交还：撤出托盘、正文退回输入框（接在已输入内容之后），不丢
  const stop = () => {
    const sid = active
    connsRef.current[sid]
      ?.stop()
      .then(({ returned = [] }) => {
        if (!returned.length) return
        dropFromTray(sid, returned.map((it) => it.message_id ?? ''))
        restoreToInput(returned.map((it) => it.text ?? ''))
      })
      .catch(() => {})
  }

  // 撤回一条待插入消息：后端确认未注入才撤出托盘、退回输入框；已注入则等 message.injected
  const withdraw = (item: PendingItem) => {
    const sid = active
    const mid = item.messageId ?? ''
    connsRef.current[sid]
      ?.withdrawMessage(mid)
      .then(({ withdrawn }) => {
        if (!withdrawn) return
        dropFromTray(sid, [mid])
        restoreToInput([item.text])
      })
      .catch(() => {})
  }

  // 退回的文本接回输入框（附件不退：已上传落盘，重发需重新添加）
  const restoreToInput = (texts: string[]) => {
    const back = texts.filter(Boolean).join('\n')
    if (back) setInput((cur) => (cur.trim() ? `${cur}\n${back}` : back))
    inputRef.current?.focus()
  }

  // 观测视图中断运行中的 cron run：调 stopCronRun（按 job_id 取消调度器里的执行），
  // 而非普通会话 stop（run 不在本会话 bridge 里）。
  const stopObservedRun = () => {
    if (liveRun) gwForBackend(activeBackend)?.stopCronRun(liveRun.job_id).catch(() => {})
  }

  const resumeWith = (value: unknown, clear: 'approval' | 'clarify') => {
    // 应答队首：approval_id 取自队首挂起项回发给在途审批 Broker，并乐观出队（下一条浮现）。
    const queue = storeRef.current[active]?.[clear] ?? []
    const head = queue[0]
    if (!head) return
    const headId = (head as { approval_id?: string }).approval_id ?? ''
    setStore((s) => ({
      ...s,
      [active]: { ...s[active], [clear]: (s[active]?.[clear] ?? []).slice(1) },
    }))
    connsRef.current[active]?.resume(headId, value).catch(() => {
      resetRunning(active)
      // RPC 未达后端（连接抖动）→ 回滚出队，保留该卡供重连后重试；按 approval_id 去重，
      // 避免与后端重发的卡片重复。否则队首消失而后端 Future 仍挂、轮卡死。
      setStore((s) => {
        if (!s[active]) return s
        const q = s[active][clear] ?? []
        return q.some((x) => (x as { approval_id?: string }).approval_id === headId)
          ? s
          : { ...s, [active]: { ...s[active], [clear]: [head, ...q] } }
      })
    })
  }

  // 逐个审批：decisions 与 tool_calls 同序；后端部分拒绝时执行已允许的、被拒的附说明回模型继续
  const decide = (decisions: Decision[]) =>
    resumeWith({ decisions, message: t('approval.rejectedMessage') }, 'approval')

  const streaming = items.some((it) => it.kind === 'assistant' && it.streaming)
  const hasMessages = items.length > 0 || !!cur?.historyCursor || !!cur?.historyUnavailable
  let historyStatus: ReactNode = null
  if (cur?.historyLoading) {
    historyStatus = <><span className="lumi-orb" />{t('chat.historyLoading')}</>
  } else if (cur?.historyError) {
    historyStatus = (
      <button onClick={() => void loadOlderHistory(active)} title={cur.historyError} className="hover:text-ink">
        {t('chat.historyRetry')}
      </button>
    )
  } else if (cur?.historyUnavailable) {
    historyStatus = t('chat.historyUnavailable')
  } else if (cur?.historyCursor) {
    historyStatus = t('chat.historyMore')
  }
  // 时间旅行操作（重新生成/编辑重发）可用：空闲、已连接、非渠道/cron 旁观、无在途审批
  const canTimeTravel =
    conn === 'open' && !running && !observingCronRun && !activeChannel && !approval && !clarify
  // 原地编辑中的用户气泡 id（一次只编辑一条；Cancel/切会话即退出，Save 前无任何实际修改）
  const [editingId, setEditingId] = useState<Item['id'] | null>(null)
  useEffect(() => setEditingId(null), [active])
  // 连续工具分段只随 items 变化重算，避免每次渲染都扫描
  const segments = useMemo(() => groupItems(items), [items])
  // 复制按钮挂在每轮「最后一个 segment」之后——即整段助手输出的底部（像 Claude 的动作栏
  // 在消息末尾），而不是夹在文字与其后工具（如 ask）之间。规则：
  // - 一轮文字↔工具交错时，中间文字段是过程，不给复制；只复制本轮**最终**那段助手文字。
  // - 按钮落点 = 本轮最后一个可锚定 segment（文字本身，或其后的工具如 ask）下方；
  //   错误气泡（notice）不占锚点，避免复制按钮挂到红色错误下面。
  // - replyMap: segment key → 该轮最终助手消息（正文与分支锚点）；activeKey: 末轮的锚点 key。
  //   渲染时只对 activeKey 这一条按 running 把关（历史轮始终可复制）；末轮 running=true
  //   （生成中 / ask 等中断 pending）不出，收尾（完成或 stop→turn.complete）才出。
  const { replyMap, activeKey } = useMemo(() => {
    const map = new Map<string, AssistantItem>()
    let reply: AssistantItem | null = null
    let lastKey: string | null = null // 本轮最后一个可锚定 segment 的 key
    for (const seg of segments) {
      const kind = seg.kind === 'item' ? seg.item.kind : 'tools'
      if (kind === 'user') {
        if (reply && lastKey) map.set(lastKey, reply) // 收尾上一轮
        reply = null
        lastKey = null
      } else if (kind !== 'notice' && kind !== 'retry') {
        // 助手文字/工具才能锚定复制按钮；错误气泡(notice)/重试提示跳过，不占锚点
        lastKey = segKey(seg)
        if (seg.kind === 'item' && seg.item.kind === 'assistant' && seg.item.text) {
          reply = seg.item
        }
      }
    }
    let activeKey: string | null = null
    if (reply && lastKey) {
      map.set(lastKey, reply) // 末轮收尾，记锚点供 running 把关
      activeKey = lastKey
    }
    return { replyMap: map, activeKey }
  }, [segments])

  // 斜杠命令补全：命令模式下按前缀过滤，菜单可被 Esc 临时关闭
  const cmdMode = isCommandMode(input)
  const matched = useMemo(
    () => (cmdMode ? matchCommands(commands, input.slice(1)) : []),
    [cmdMode, input, commands],
  )
  const menuOpen = cmdMode && !cmdDismissed && matched.length > 0

  // 要高亮的 "/命令" token：命令模式下按前缀亮，带参数时仅精确命中才亮
  const cmdToken = useMemo(() => {
    if (!input.startsWith('/')) return ''
    const tok = input.slice(1).split(/[\s\n]/, 1)[0]
    if (!tok) return ''
    const hasArgs = /[\s\n]/.test(input)
    const ok = hasArgs
      ? commands.some((c) => c.name === tok)
      : commands.some((c) => c.name.startsWith(tok))
    return ok ? `/${tok}` : ''
  }, [input, commands])

  // 选中命令：填充 "/name "（尾随空格关闭菜单），焦点留在输入框
  const pickCommand = (cmd: SlashCommand) => {
    setInput(`/${cmd.name} `)
    setCmdDismissed(false)
    inputRef.current?.focus()
  }

  const onComposerChange = (v: string) => {
    if (isCommandMode(v) && !cmdMode) loadCommands() // 进入命令模式时刷新（技能动态）
    setCmdDismissed(false)
    setCmdSel(0)
    setInput(v)
  }

  // 输入栏发送路由：项目主页复用同一输入栏，发送即在此项目新建会话并携带首条消息；
  // 其余视图（聊天/欢迎页）发往当前会话。回车与发送键共用。
  const submitCurrent = () => ((view === 'project' || view === 'newchat') ? void startProjectChat() : send())

  const onComposerKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法组合中（拼音选字等）的按键全部交给 IME：选字回车不应触发发送/菜单确认
    if (e.nativeEvent.isComposing) return
    if (menuOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setCmdSel((s) => (s + 1) % matched.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setCmdSel((s) => (s - 1 + matched.length) % matched.length)
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        // 钳制：commands 异步刷新可能使 matched 缩短而 cmdSel 未重置，避免越界取 undefined
        pickCommand(matched[Math.min(cmdSel, matched.length - 1)])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setCmdDismissed(true)
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submitCurrent()
    }
  }

  // 飞书渠道会话：desktop 只读旁观。
  // 横幅数据：群名取会话列表 title（入站写的 channel_title），审批模式/绑定项目取渠道快照。
  // 按 thread 前缀定归属机器人——多机器人时行 name 全是 'feishu'，按 name find 会恒取第一行
  const feishuInfo = botOfThread(
    channels[activeBackend] ?? [],
    activeSession?.thread_id ?? '',
  )
  const channelBanner = () => {
    const proj = feishuInfo?.config.workspace ? basename(feishuInfo.config.workspace) : ''
    return (
      <div className="mx-auto w-full max-w-3xl px-6 pt-3">
        <div className="flex items-center gap-2.5 rounded-xl border border-info/25 bg-info/10 px-3.5 py-2 text-xs">
          <Send size={14} className="shrink-0 text-info" />
          <div className="min-w-0 flex-1 truncate">
            <span className="font-medium text-ink">
              {t('sidebar.feishu')}
              {activeSession?.title ? ` · ${activeSession.title}` : ''}
            </span>
            <span className="ml-2 text-muted-foreground">
              {feishuInfo && t(`chan.mode.${feishuInfo.config.tool_mode}`)}
              {proj ? ` · ${t('chan.boundProject', { name: proj })}` : ''}
            </span>
          </div>
          <button
            onClick={() => {
              setSettingsTab('channels')
              setShowSettings(true)
            }}
            className="shrink-0 text-info hover:underline"
          >
            {t('chan.settings')} ›
          </button>
        </div>
      </div>
    )
  }
  // 只读提示条：替换渠道会话的输入框（send 已封禁，这里是视觉层）。
  // 右侧挂上下文环——旁观会话看不到实时流，但每轮结束重拉的快照带 usage/窗口，
  // 分母取会话真实模型（cur.ctxWindow）而非本地选择器的模型。无数据时环自隐藏、文字仍居中。
  const readonlyBar = (
    <div className="flex items-center gap-2 rounded-3xl border border-dashed border-line bg-surface/50 py-2 pl-4 pr-2.5 text-xs text-muted-foreground">
      <span className="flex-1 text-center">{t('chan.readonly')}</span>
      <ContextMeter usage={cur?.ctx} window={cur?.ctxWindow ?? 0} model={cur?.ctxModel ?? ''} />
    </div>
  )

  const hasDraft = input.trim() !== '' || attachments.length > 0

  // project=true：项目主页复用本输入栏。此刻无「活动会话」，故把三处与当前会话绑定的
  // 部件解耦——不显示上下文用量环（不读 cur.ctx，免后台流式 token 触发项目页重渲染）、
  // 永远显示发送键（不读活动会话 running）、隐藏文件夹菜单（会话级授权，发送前无会话可挂）。
  // 其余（斜杠命令/附件/模型选择/审批模式）与聊天页完全一致。
  const composer = (placeholder: string, project = false) => (
    <div>
      {menuOpen && (
        <CommandMenu
          commands={matched}
          selected={cmdSel}
          onPick={pickCommand}
          onHover={setCmdSel}
        />
      )}
      {/* 聚焦描边在 index.css 的 .composer-glass:focus-within（非分层规则），
          在此加 focus-within: 工具类会被它压过、不生效 */}
      <div
        className="composer-glass rounded-3xl transition-colors overflow-hidden"
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDropFiles}
      >
      {/* 待插入托盘：运行中发出、等下一个工具边界送达的消息（注入后移入消息流） */}
      {!project && pending.length > 0 && (
        <div className="flex flex-col gap-1.5 px-3.5 pt-3">
          {pending.map((p) => (
            <div
              key={p.id}
              className="interject-chip subtool-enter flex items-center gap-2 rounded-xl px-2.5 py-1.5 text-xs"
            >
              <span className="lumi-orb lumi-orb-sm" />
              <span className="min-w-0 flex-1 truncate text-ink">
                {p.text || p.files?.map((f) => f.name).join(', ') || t('composer.pendingImage')}
              </span>
              <span className="shrink-0 text-[10.5px] text-muted-foreground">
                {t('composer.pending')}
              </span>
              <button
                onClick={() => withdraw(p)}
                aria-label={t('composer.withdraw')}
                title={t('composer.withdraw')}
                className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-ink hover:bg-line/60 transition"
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 px-3.5 pt-3">
          {attachments.map((a) =>
            a.kind === 'image' ? (
              <div key={a.id} className="attachment-arrive relative group/att">
                <img
                  src={a.dataUrl}
                  alt={a.name}
                  className="size-16 object-cover rounded-xl border border-line/40"
                />
                <button
                  onClick={() => removeAttachment(a.id)}
                  aria-label={t('composer.removeAttachment')}
                  className="absolute -top-1.5 -right-1.5 size-5 grid place-items-center rounded-full bg-canvas border border-line text-muted-foreground hover:text-ink opacity-0 group-hover/att:opacity-100 transition"
                >
                  <X size={12} />
                </button>
              </div>
            ) : (
              <div
                key={a.id}
                title={a.path}
                className="attachment-arrive relative group/att flex items-center gap-2 max-w-56 h-9 pl-2 pr-2.5 rounded-xl border border-line/40 bg-canvas"
              >
                <FileText size={15} className="shrink-0 text-muted-foreground" />
                <span className="min-w-0 truncate text-xs text-ink">{a.name}</span>
                <button
                  onClick={() => removeAttachment(a.id)}
                  aria-label={t('composer.removeAttachment')}
                  className="absolute -top-1.5 -right-1.5 size-5 grid place-items-center rounded-full bg-canvas border border-line text-muted-foreground hover:text-ink opacity-0 group-hover/att:opacity-100 transition"
                >
                  <X size={12} />
                </button>
              </div>
            ),
          )}
        </div>
      )}
      <Composer
        value={input}
        onChange={onComposerChange}
        onKeyDown={onComposerKey}
        onPaste={onPasteImages}
        // 项目主页看目标机器的连接（没有会话连接），聊天页看活动会话的
        disabled={(project ? pickerConn : conn) !== 'open' || observingCronRun}
        placeholder={!project && running ? t('composer.interject') : placeholder}
        highlightLen={cmdToken.length}
        inputRef={inputRef}
      />
      <div className="composer-toolbar flex items-center justify-between gap-3 px-3 pb-2.5">
        <div className="composer-tools flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => fileInputRef.current?.click()}
            aria-label={t('composer.attach')}
            className="text-muted-foreground"
          >
            <Plus />
          </Button>
          {/* 项目主页指向的是「新会话默认」（那里还没有会话，active 是上一个），
              聊天页指向本会话的模型 */}
          <div className="composer-model"><ModelPicker
            providers={providers}
            active={project ? defaultModel : sessionModel}
            machine={{
              id: pickerBackend,
              name: machineName(pickerBackend, machines),
              color: machineColor(pickerBackend, machines),
            }}
            multi={machines.filter((m) => m.enabled !== false).length > 1}
            project={basename(project ? homeTarget?.path ?? '' : workspaceDir)}
            onSwitch={(p, m) => switchModel(p, m, project)}
            onSwitchEffort={(lv) => switchEffort(lv, project ? defaultModel : sessionModel)}
          />
          </div><div className="composer-approval"><ApprovalModePicker
            value={toolMode}
            onChange={(m) => {
              setToolMode(m)
              // 实时推后端：改运行中会话的共享 context，对当前轮后续工具立即生效。
              // 未连接时静默失败——下一条消息自带 tool_mode 会重设 context。
              // 项目模式无活动会话，不推 active（它是上个会话）；toolMode 发送时随新会话生效。
              if (!project) connsRef.current[active]?.setToolMode(m).catch(() => {})
            }}
            classifierLabel={classifier.provider ? classifier.model : undefined}
          /></div>
        </div>
        <div className="composer-submit flex items-center gap-1.5">
          {/* 项目模式无活动会话：不读 cur.ctx（免后台流式 token 触发项目页重渲染），整块不渲染 */}
          {!project && <ContextMeter usage={cur?.ctx} window={contextWindow} model={model} />}
          {/* 单键变形：运行中输入框空 = 停止，有内容 = 发送（插话） */}
          {!project &&
          (observingCronRun || (running && !hasDraft)) &&
          !approval &&
          !clarify ? (
            <Button
              size="icon"
              variant="destructive"
              onClick={observingCronRun ? stopObservedRun : stop}
              aria-label={t('composer.stop')}
              className="rounded-full"
            >
              <Square fill="currentColor" strokeWidth={0} className="size-3" />
            </Button>
          ) : (
            <Button
              size="icon"
              onClick={submitCurrent}
              disabled={!hasDraft || (project && (!homeTarget?.path || pickerConn !== 'open')) || (!project && conn !== 'open')}
              aria-label={t('composer.send')}
              className="rounded-full"
            >
              <span className="text-lg leading-none">↑</span>
            </Button>
          )}
        </div>
      </div>
      </div>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) addFiles(e.target.files)
          e.target.value = ''
        }}
      />
    </div>
  )

  // 项目主页输入岛 = 同一套输入栏（project=true）。ProjectHomePage 是 memo 的，slot 若每次
  // App 渲染都换新元素会击穿 memo（后台流式 token 触发整页 reconcile），故 useMemo 稳定住。
  // deps 列全 project 模式 composer 读到的交互态——独不含流式 store，后台 token 便不重算此 slot。
  const projectComposer = useMemo(
    () => composer(t('projhome.composerPlaceholder', { name: homeProjectInfo?.name ?? '' }), true),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // defaultModel 不可省：project 模式的选择器指着「新会话默认」（那里还没有会话），
    // 漏了它就出现「切了模型 chip 纹丝不动、后端却已改」
    [
      input, attachments, conn, pickerConn, model, providers, sessionModel, defaultModel, machines,
      activeBackend, toolMode, classifier, menuOpen, matched, cmdSel, cmdToken,
      homeProjectInfo?.name,
    ],
  )

  // ── 统一右栏派生量（布尔量廉价，每渲染重算即可；数组 memo 是为 RunsSection 的 memo 不被击穿）──
  // 当前定时任务的直播执行：喂 RunsSection 活条目 + 收起态脉冲点
  const cronLiveRuns = useMemo(
    () =>
      view === 'cronjob' && activeCronJob
        ? (cronActiveRuns[cronBackendOf(activeCronJob)] ?? []).filter(
            (r) => r.job_id === activeCronJob && r.thread_id,
          )
        : [],
    [view, activeCronJob, cronActiveRuns, cronBackendOf],
  )
  const pickRun = useCallback(
    (tid: string) => void openRunThread(tid, cronBackendOf(activeCronJob ?? '')),
    [openRunThread, cronBackendOf, activeCronJob],
  )
  // cron 视图里后台任务模块只在「当前会话确实是本任务的某次执行」时显示：任务还没跑过
  // （run_threads 空）时 active 仍指向先前的聊天会话，不加此闸会把无关会话的后台任务
  // 挂进 cron 右栏，停止/移除还真能杀错任务
  const railSessionVisible =
    view === 'chat' || (!!cronRunThread && keyThread(active) === cronRunThread)
  const railBg = railSessionVisible && traditionalBgTasks.length > 0
  const railAgents = railSessionVisible && (subagents.agents.length > 0 || !!subagents.listError)
  const selectedSubagent = railSessionVisible ? subagents.agents.find(agent => agent.agent_id === subagents.selectedId) : undefined
  const subagentExpanded = !!selectedSubagent && subagents.expanded
  const subagentSide = !!selectedSubagent && !subagentExpanded
  const hasPreview =
    (view === 'chat' || view === 'cronjob') && preview.owner === active && preview.files.length > 0 && !subagentSide && !subagentExpanded
  const previewExpanded = hasPreview && preview.expanded && railOpen
  const rightRailWidth = hasPreview || subagentSide ? previewW : railW
  const railSectionsOpen = railOpen && !previewExpanded
  const showRail = hasPreview || (view === 'cronjob' ? !!activeCronJob : view === 'chat' && (railBg || railAgents))
  // 脉冲点 = 可见模块里确有东西在跑：隐藏的 bg 模块不算，直播中的 cron 执行算
  const railDot = (railSessionVisible && hasRunningBg) || cronLiveRuns.length > 0

  const machinesCtx = useMemo(
    () => ({ machines, conn: machineConn, err: machineErr, reconnect: reconnectMachine }),
    [machines, machineConn, machineErr, reconnectMachine],
  )

  function renderMainContent(): ReactNode {
    if (subagentExpanded && selectedSubagent) return <SubagentDetail
      key={`${active}:${selectedSubagent.agent_id}`}
      agent={selectedSubagent} detail={subagents.detail} position={subagents.position} expanded
      remote={remoteBackend} online={conn === 'open'}
      onBack={subagents.close} onExpand={toggleSubagentExpanded} onRetry={subagents.retryDetail} onOpenFile={openPreview}
    />
    if (view === 'newchat') return (
      <div className="new-chat-home flex-1 flex flex-col items-center justify-center px-6">
        <HomeBackdrop />
        <div className="mb-9 text-center"><h1 className="serif text-2xl">{t('ssh.welcome')}</h1></div>
        <div className="new-chat-entry w-full max-w-[760px]">
          <NewChatTarget value={newTarget} onChange={target => { newTargetRevision.current++; setNewTarget(target) }} ensureMachine={ensureMachine} />
          {composer(t('composer.empty'), true)}
        </div>
      </div>
    )
    if (view === 'projects') return (
      <ProjectsPage
        projects={projects}
        current={projectsMachine === activeBackend ? workspaceDir : ''}
        machine={projectsMachine}
        needProjectHint={needProjectHint}
        onSelectMachine={selectProjectsMachine}
        // 点项目一律进项目主页——主页输入岛本身就能在此项目开聊，故无论是主动浏览
        // 还是被「新建会话」阻断跳来（needProjectHint），落点一致，不再偶发跳到欢迎页。
        onOpen={(p) => openProjectHome(p, projectsMachine)}
        onNew={() => setShowNewProject(true)}
        onRemove={(path) =>
          setPendingRemoveProject(projects?.find((p) => p.path === path) ?? null)
        }
        onRename={(path, name) => renameProjectInList(path, name, projectsMachine)}
        onSetDefault={(path, isDefault) => setProjectDefault(path, isDefault, projectsMachine)}
      />
    )
    if (view === 'project' && homeProjectInfo) return (
      <ProjectHomePage
        key={`${projectHome?.backend ?? 'local'}:${homeProjectInfo.path}`}
        project={homeProjectInfo}
        machine={projectHome?.backend ?? 'local'}
        isDefault={!!homeProject?.default}
        api={projectHomeApi}
        sessions={homeSessions}
        cronJobs={homeCronJobs}
        composerSlot={projectComposer}
        onOpenSession={openHomeSession}
        onOpenScheduled={openHomeScheduled}
        onToggleCron={toggleHomeCron}
      />
    )
    if (view === 'scheduled') return (
      <CronPage
        api={gwForBackend}
        initialMachine={cronEntry.machine}
        initialJobId={cronEntry.jobId}
        createIn={cronEntry.createIn}
        jobs={cronJobs}
        runningJobs={cronRunning}
        version={cronVersion}
        onOpenRun={(tid, jid) => void openCronJob(jid, tid)}
        onRefresh={refreshCronJobs}
      />
    )
    return (
      <div className="flex-1 flex min-h-0">
        <div className="flex-1 flex flex-col min-w-0">
          {view === 'cronjob' && !cronRunThread ? (
            // 任务还没有可查看的执行会话：显示空态，避免把消息误发进无关会话
            <div className="flex-1 grid place-items-center text-sm text-muted-foreground select-none">
              {t('cron.noRuns')}
            </div>
          ) : view === 'cronjob' && !hasMessages ? (
            // 乐观切换后历史尚未加载：占位而非欢迎页——欢迎页带可编辑输入框，
            // 手快会把消息误发进定时任务执行线程
            <div className="flex-1 grid place-items-center text-sm text-muted-foreground select-none">
              {t('chat.loading')}
            </div>
          ) : hasMessages ? (
            <>
              {activeChannel && channelBanner()}
              <div className="relative flex-1 min-h-0">
                {/* 顶栏渐隐：内容滚到顶栏时淡出而非硬切。高度不超过 chat-body 的 py-8，
                        停在顶部时首条内容不被遮；right-2 让开滚动条 */}
                <div className="chat-top-fade pointer-events-none absolute left-0 right-2 top-0 z-10 h-8" />
                <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-auto" style={{ overflowAnchor: 'none' }}>
                  <div className="chat-body max-w-3xl mx-auto w-full px-6 py-8 space-y-5">
                    {historyStatus && (
                      <div className="h-6 flex justify-center items-center gap-2.5 text-xs text-muted-foreground" role="status">
                        {historyStatus}
                      </div>
                    )}
                    {segments.map((seg) => {
                      const key = seg.kind === 'item' && seg.item.kind === 'user' && seg.item.arrival
                        ? `arrival:${seg.item.arrival.id}` : segKey(seg)
                      const node =
                        seg.kind === 'tools' ? (
                          <ToolGroup key={key} tools={seg.tools} onOpenSubagent={openSubagent} />
                        ) : seg.kind === 'agent' ? (
                          <AgentGroup key={key} items={seg.items} />
                        ) : seg.kind === 'files' ? (
                          <FileCards
                            key={key}
                            files={seg.files}
                            onOpen={openPreview}
                            activePath={hasPreview ? preview.activePath ?? undefined : undefined}
                            remote={remoteBackend}
                          />
                        ) : (
                          <ItemView key={key} item={seg.item} fileSrc={thumbSrc} onOpenImage={openImage} />
                        )
                      // 用户气泡编辑态：气泡原位换成可编辑框（Save 前零副作用）
                      const userItem =
                        seg.kind === 'item' && seg.item.kind === 'user' ? seg.item : null
                      if (userItem && editingId === userItem.id) {
                        return (
                          <div key={key} data-history-id={key}>
                            <EditBubble
                              initial={userItem.text}
                              onCancel={() => setEditingId(null)}
                              onSave={(text) => timeTravel(userItem.id, text)}
                            />
                          </div>
                        )
                      }
                      // 悬停操作条：用户气泡挂时间旅行 + 复制（空闲且非旁观才给截断类
                      // 操作），助手轮挂复制——末轮（activeKey）在飞时不给，历史轮恒可。
                      // 两侧共用同一个 HoverActions，悬停显隐的几何与时序只此一处。
                      let copyText: string | undefined
                      if (userItem) copyText = userItem.text
                      else if (key !== activeKey || !running) copyText = replyMap.get(key)?.text
                      const timeTravelable = userItem && canTimeTravel && !!userItem.messageId
                      const forkPoint = !userItem && canTimeTravel ? replyMap.get(key)?.forkPoint : undefined
                      if (!copyText && !timeTravelable) return <div key={key} data-history-id={key}>{node}</div>
                      return (
                        <div key={key} data-history-id={key}>
                          <HoverActions node={node} align={userItem ? 'end' : 'start'}>
                            {timeTravelable && (
                              <>
                                <IconAction
                                  icon={RotateCcw}
                                  label={t('chat.regenerate')}
                                  onClick={() => timeTravel(userItem.id)}
                                />
                                <IconAction
                                  icon={Pencil}
                                  label={t('chat.edit')}
                                  onClick={() => setEditingId(userItem.id)}
                                />
                              </>
                            )}
                            {copyText && <CopyButton text={copyText} />}
                            {forkPoint && (
                              <ForkButton
                                busy={forking === `${active}:${forkPoint.checkpoint_id}:${forkPoint.message_id}`}
                                disabled={forking !== null}
                                onClick={() => void forkChat(forkPoint)}
                              />
                            )}
                          </HoverActions>
                        </div>
                      )
                    })}
                    {/* 运行 / 等待 / 后台任务显示状态，无活动时整行隐藏，历史留在右栏。 */}
                    <StatusIndicator
                      items={items}
                      running={running || observingCronRun}
                      runStart={liveRun ? Date.parse(liveRun.started_at) : runStart}
                      waiting={!!(approval || clarify)}
                      streaming={streaming}
                      thinkingText={thinkingText}
                      compacting={compacting}
                      backgroundCount={runningBgCount}
                      onOpenTasks={() => {
                        if (previewExpanded) dispatchPreview({ type: 'toggle-expanded' })
                        if (!railOpen) toggleRail()
                      }}
                      failed={conn === 'failed'}
                    />
                  </div>
                </div>
                {showJump && (
                  <button
                    type="button"
                    onClick={jumpToBottom}
                    title={t('chat.toBottom')}
                    aria-label={t('chat.toBottom')}
                    className="scroll-jump absolute bottom-4 left-1/2 -ml-[17px]"
                  >
                    <ChevronDown className="h-[18px] w-[18px]" />
                  </button>
                )}
              </div>
              <div className="px-6 pb-5">
                <div className="max-w-3xl mx-auto w-full">
                  {/* 审批/澄清：渲染在输入框上方，切走时随会话留在原处 */}
                  {/* key=approval_id：队首换成下一条挂起项时重置分步状态 */}
                  {approval && (
                    <ApprovalDialog
                      key={String(approval.approval_id)}
                      data={approval}
                      onSubmit={decide}
                    />
                  )}
                  {clarify && (
                    <ClarifyDialog
                      key={String(clarify.approval_id)}
                      data={clarify}
                      onSubmit={(answer) => resumeWith(answer, 'clarify')}
                      onCancel={() => resumeWith(ASK_CANCELLED, 'clarify')}
                    />
                  )}
                  {activeChannel ? readonlyBar : composer(t('composer.reply'))}
                </div>
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center px-6 -mt-8">
              <div className="mb-8 flex items-center gap-2.5 select-none">
                <span className="text-primary text-3xl">✦</span>
                <span className="serif text-3xl">Lumi</span>
              </div>
              <div className="w-full max-w-2xl">
                {activeChannel ? readonlyBar : composer(t('composer.empty'))}
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    // 机器连接态 + 重连入口下发给所有「先选机器」的界面（设置各面板 / 项目页 / 定时页），
    // 它们据此在机器离线时停掉配置读写入口（见 MachineScope）。memo 化：App 每个流式
    // token 都重渲染，裸对象会让所有消费者跟着一起重渲
    <MachinesProvider value={machinesCtx}>
    <SshPrompts />
    <div className={`app-canvas h-full flex flex-col bg-canvas${view === 'newchat' && !subagentExpanded ? ' app-home' : ''}`}>
      {!isMacTitleBar && (
        <AppTitleBar onNewChat={startNewChat} onOpenSettings={openSettings} />
      )}
      <div className="relative min-h-0 flex-1 flex">
      {/* 悬浮侧栏占位：宽度动画（侧栏本体绝对定位于其中，收起时向左滑出） */}
      <div
        className="relative shrink-0 transition-[width] duration-300 ease-out"
        style={{ width: sidebarOpen ? sidebarW.width + FLOAT_GAP * 2 : 0, display: previewExpanded ? 'none' : undefined }}
      >
      <Sidebar
        width={sidebarW.width}
        open={sidebarOpen}
        onToggle={toggleSidebar}
        showTitleDrag={isMacTitleBar}
        sessions={sessions}
        loadedBackends={loadedBackends}
        machines={machines}
        channels={channels}
        currentKey={view === 'chat' ? active : ''}
        conn={conn}
        backend={activeBackend}
        workspace={workspaceDir}
        activity={activity}
        projectsActive={view === 'projects' || view === 'project'}
        scheduledActive={view === 'scheduled'}
        onSelect={selectSession}
        // 直接传稳定引用：内联箭头每次渲染换身份，击穿 Sidebar 的 memo（每个流式 token 整棵重渲）
        onNew={startNewChat}
        onNewChat={goNewChat}
        onNewChatIn={newSession}
        onOpenProjects={openProjects}
        onOpenScheduled={openScheduled}
        onOpenSettings={openSettings}
        onPin={pinSession}
        onRename={renameSession}
        onDelete={setPendingDelete}
      />
      </div>
      {/* floating：把手从占位容器边缘贴回悬浮面板的可见右缘 */}
      {sidebarOpen && !previewExpanded && <ResizeHandle {...sidebarW} edge="right" floating />}

      <main className="flex-1 flex flex-col min-w-0" style={previewExpanded ? { display: 'none' } : undefined}>
        {showTopStrip && (
          // 拖拽区与按钮区并排分离（互不重叠的矩形）：不在 drag 大条上给按钮挖洞——
          // 洞依赖 Chromium 的区域重采样，实测会因动画/布局时序失效导致按钮下半不可点。
          // 高度恒定不随 sidebarOpen 变（曾 h-9↔h-14 切换，收放侧栏时主区内容整体上下跳）。
          <div
            className={`${isMacTitleBar ? 'h-9' : 'h-10'} shrink-0 flex items-stretch`}
          >
            {!sidebarOpen && (
              // titlebar-interactive 见 index.css（macOS 26 命中偏移的合成层修复）；
              // pl-[100px] 让开红绿灯（坐标见 main.cjs trafficLightPosition，改灯位需同步）。
              // mac 红绿灯固定在 (26,20)：条带恒 h-9 后，由本容器 translate 下移让按钮对齐
              // 灯的中心线（y≈27）。位移必须在容器而非按钮上——Button 基类的 active:
              // translate-y-px 会在按下瞬间覆盖按钮自身的 translate-y，导致按下跳 8px。
              // 容器下移后底部 10px 压在主区内容上且合成层命中序在前：pointer-events-none
              // 让点击穿透容器（含 100px 纯 padding 区），按钮自己 auto 恢复命中
              <div
                className={`titlebar-interactive pointer-events-none flex items-center ${
                  isMacTitleBar ? 'pl-[100px] translate-y-[10px]' : 'pl-3'
                }`}
              >
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={toggleSidebar}
                  title={t('sidebar.expand')}
                  className="toggle-fade-in pointer-events-auto -translate-y-px text-muted-foreground hover:text-ink"
                >
                  <PanelLeft />
                </Button>
              </div>
            )}
            {/* 中段纯拖拽条：独立矩形，与按钮区互不重叠，无需挖洞。
                会话标题只读无交互，放在拖拽条内不影响拖窗；mac 同按钮区下移对齐红绿灯中心线。
                右栏按钮绝对定位在聊天区末尾 40px 内，预留 48px 避免标题 / 项目标签与它重叠 */}
            <div
              className={`flex-1 min-w-0 flex items-center ${isMacTitleBar ? 'app-drag' : ''} ${showRail ? 'pr-12' : ''}`}
            >
              {view === 'chat' && activeSession && (
                <div
                  className={`flex min-w-0 items-center gap-2 select-none ${sidebarOpen ? 'pl-4' : 'pl-1'} ${
                    isMacTitleBar ? 'translate-y-[10px]' : ''
                  }`}
                >
                  <MachineIcon id={activeBackend} size={14} />
                  <span className="truncate text-[13px] font-semibold text-ink">
                    {activeSession.title || activeSession.first_message}
                  </span>
                  {activeSession.workspace_dir && (
                    <span className="shrink-0 rounded-[5px] bg-surface px-1.5 text-[11.5px] text-muted-foreground">
                      {basename(activeSession.workspace_dir)}
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
        {renderMainContent()}
      </main>
      {/* 预览、执行记录与后台任务共用一列浮卡；放大时只显示预览工作区。 */}
      {showRail && (
        <>
          {railSectionsOpen && <ResizeHandle {...rightRailWidth} edge="left" />}
          <RightRail
            width={rightRailWidth.width}
            open={railOpen}
            expanded={previewExpanded}
            onToggle={toggleRightRail}
            dot={railDot}
            enter={view === 'chat' && !hasPreview}
          >
            {subagentSide && selectedSubagent && <SubagentDetail
              key={`${active}:${selectedSubagent.agent_id}`}
              agent={selectedSubagent} detail={subagents.detail} position={subagents.position} expanded={false}
              remote={remoteBackend} online={conn === 'open'}
              onBack={subagents.close} onExpand={toggleSubagentExpanded} onRetry={subagents.retryDetail} onOpenFile={openPreview}
            />}
            {hasPreview && (
              <PreviewWindow
                key={active}
                files={preview.files}
                activePath={preview.activePath}
                expanded={previewExpanded}
                nativeTitleBar={isMacTitleBar}
                gw={controlConns.current[activeBackend]}
                remote={remoteBackend}
                onSelect={selectPreview}
                onCloseTab={closePreviewTab}
                onClose={closeAllPreviews}
                onToggleExpanded={togglePreviewExpanded}
              />
            )}
            <div className={previewExpanded || subagentSide ? 'hidden' : 'contents'} inert={previewExpanded || subagentSide}>
              {railAgents && <SubagentsSection agents={subagents.agents} tasks={activeBgTasks} selectedId={subagents.selectedId} error={subagents.listError} onSelect={openSubagent} onStop={stopSubagent} />}
              {view === 'cronjob' && activeCronJob && (
                // key=任务 id：换任务即重挂，节折叠态不跨任务残留（整栏开合在 App，不受影响）
                <RunsSection
                  key={activeCronJob}
                  api={runsRailApi}
                  machine={cronBackendOf(activeCronJob)}
                  jobId={activeCronJob}
                  open={railSectionsOpen}
                  activeThread={cronRunThread}
                  readRuns={readRuns}
                  version={cronVersion}
                  liveRuns={cronLiveRuns}
                  onPick={pickRun}
                />
              )}
              {railBg && (
                <BgTasksSection
                  tasks={traditionalBgTasks}
                  open={railSectionsOpen}
                  onStop={stopBgTask}
                  onDismiss={dismissBgTask}
                  onReadOutput={readBgTaskOutput}
                  onClearFinished={clearFinishedBgTasks}
                />
              )}
            </div>
          </RightRail>
        </>
      )}

      {showSettings && (
        <SettingsDialog
          initialTab={settingsTab}
          themePref={themePref}
          setThemePref={setThemePref}
          uiFont={uiFont}
          setUiFont={setUiFont}
          notify={notify}
          setNotify={toggleNotify}
          gwFor={gwForBackend}
          onProvidersChanged={onProvidersChanged}
          onClose={() => {
            setShowSettings(false)
            refreshChannels() // 渠道配置可能在设置里改过：同步侧栏状态灯与旁观横幅
          }}
        />
      )}
      {showNewProject && (
        <DirBrowser
          gw={gwForBackend(projectsMachine)}
          machine={projectsMachine}
          onPick={(p) => void createProject(p, basename(p), projectsMachine)}
          onCancel={() => setShowNewProject(false)}
        />
      )}
      {pendingRemoveProject && (
        <ConfirmDialog
          title={t('projects.removeTitle')}
          message={t('projects.removeMessage', { name: pendingRemoveProject.name })}
          confirmLabel={t('projects.remove')}
          onConfirm={() => {
            removeProjectFromList(pendingRemoveProject.path, projectsMachine)
            setPendingRemoveProject(null)
          }}
          onCancel={() => setPendingRemoveProject(null)}
        />
      )}
      {pendingDelete && (
        <ConfirmDialog
          title={t(pendingDelete.channel ? 'confirm.clearSessionTitle' : 'confirm.deleteTitle')}
          message={t(pendingDelete.channel ? 'confirm.clearSessionMessage' : 'confirm.deleteMessage', {
            name: clip(pendingDelete.title || pendingDelete.first_message || t('sidebar.untitled'), 48),
          })}
          onConfirm={() => deleteSession(pendingDelete)}
          onCancel={() => setPendingDelete(null)}
        />
      )}
      </div>
    </div>
    </MachinesProvider>
  )
}

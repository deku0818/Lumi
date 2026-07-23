import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  SquareTerminal,
  FileText,
  FilePlus,
  FilePen,
  Search,
  Bot,
  ListChecks,
  Wrench,
  ChevronRight,
  ChevronDown,
  Copy,
  Check,
  Square,
  Plus,
  Send,
  X,
  PanelLeft,
  type LucideIcon,
} from 'lucide-react'
import { Gateway, type ConnState } from './gateway'
import type {
  ActiveModel,
  AttachedFile,
  BgTask,
  ChannelInfo,
  ModelPointer,
  CronJob,
  HistoryItem,
  Item,
  PresentedFile,
  Project,
  ProviderProfile,
  SessionMeta,
  SlashCommand,
  SubTool,
  ToolMode,
  Usage,
  WireEvent,
  WireEventPayloads,
} from './types'
import { Markdown } from './components/Markdown'
import { ApprovalDialog } from './components/ApprovalDialog'
import { ClarifyDialog, ASK_CANCELLED } from './components/ClarifyDialog'
import { Sidebar } from './components/Sidebar'
import { FileCards, PreviewPanel, parsePresentedFiles } from './components/PresentedFiles'
import { BgTasksSection } from './components/BgTasksDrawer'
import { CronPage, RunsSection } from './components/CronPage'
import { RightRail } from './components/RightRail'
import { ResizeHandle, usePersistedFlag, useResizableWidth } from './components/ResizeHandle'
import { ConfirmDialog } from './components/ConfirmDialog'
import { SettingsDialog } from './components/SettingsDialog'
import { ModelPicker } from './components/ModelPicker'
import { ApprovalModePicker } from './components/ApprovalModePicker'
import { ContextMeter, type CtxUsage } from './components/ContextMeter'
import { ProjectHomePage } from './components/ProjectHomePage'
import { ProjectsPage } from './components/ProjectsPage'
import { DirBrowser } from './components/DirBrowser'
import { FolderMenu } from './components/FolderMenu'
import { CommandMenu } from './components/CommandMenu'
import { Composer } from './components/Composer'
import { AppTitleBar } from './components/AppTitleBar'
import { toast } from './components/Toast'
import { isCommandMode, parseCommand, matchCommands } from './slash'
import { toolDiff, type DiffLine } from './diff'
import { clip, basename, fmtTokens, machineColor, machineName, msgTime, sessionKey, keyThread, keyBackend, beOf, FLOAT_GAP } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useTheme } from './theme'
import { useUiFont } from './font'
import { useI18n } from './i18n'

// 单 app 实例，模块级自增 id 即可，避免 hook 依赖问题。
let _id = 0
const nid = () => ++_id

// 输入栏附件：图片嵌入（base64 data URL），其它文件只引用绝对路径
type Attachment =
  | { id: number; kind: 'image'; dataUrl: string; name: string }
  | { id: number; kind: 'file'; path: string; name: string }

type ToolItem = Extract<Item, { kind: 'tool' }>
type Segment =
  | { kind: 'tools'; tools: ToolItem[] }
  | { kind: 'agent'; items: ToolItem[] }
  | { kind: 'files'; item: ToolItem; files: PresentedFile[] }
  | { kind: 'item'; item: Exclude<Item, { kind: 'tool' }> }

// 把连续的 tool item 合并成一段，其余 item 各自独立 —— 用于工具分组渲染。
// 子代理（name==='agent'）也按连续合并成一段：单个渲染成滚动窗口卡片，并发多个
// 渲染成一张「N 个子 Agent」面板（每行一个 agent）。
function groupItems(items: Item[]): Segment[] {
  const segs: Segment[] = []
  for (const it of items) {
    if (it.kind === 'tool' && it.name === 'agent') {
      const last = segs[segs.length - 1]
      if (last?.kind === 'agent') last.items.push(it)
      else segs.push({ kind: 'agent', items: [it] })
    } else if (it.kind === 'tool' && it.name === 'present_files') {
      // present_files 不并入灰色工具组：单独成段，渲染成文件卡片。
      // 在此（随 items 记忆化）解析一次 JSON，避免每次渲染都 parse。
      segs.push({ kind: 'files', item: it, files: parsePresentedFiles(it.output) })
    } else if (it.kind === 'tool') {
      const last = segs[segs.length - 1]
      if (last?.kind === 'tools') last.tools.push(it)
      else segs.push({ kind: 'tools', tools: [it] })
    } else {
      segs.push({ kind: 'item', item: it })
    }
  }
  return segs
}

// segment 的稳定 React key / 复制映射 key（不依赖数组下标，切片/虚拟化也不错位）
const segKey = (seg: Segment): string =>
  seg.kind === 'tools'
    ? `g${seg.tools[0].id}`
    : seg.kind === 'agent'
      ? `a${seg.items[0].id}`
      : seg.kind === 'files'
        ? `f${seg.item.id}`
        : `i${seg.item.id}`

// load_history 的历史项 → 前端 Item
function restore(h: HistoryItem): Item {
  if (h.kind === 'user')
    return { id: nid(), kind: 'user', text: h.text ?? '', images: h.images, files: h.files, sender: h.sender, ts: h.ts }
  if (h.kind === 'assistant')
    return { id: nid(), kind: 'assistant', text: h.text ?? '', streaming: false }
  return {
    id: nid(),
    kind: 'tool',
    toolCallId: h.tool_call_id ?? '',
    name: h.name ?? '',
    args: h.args,
    output: h.output ?? '',
    done: true,
  }
}

// 把流式文本追加到最后一个仍在流式中的 assistant item；没有则新建。
function appendDelta(items: Item[], text: string): Item[] {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind === 'assistant' && it.streaming) {
      const copy = items.slice()
      copy[i] = { ...it, text: it.text + text }
      return copy
    }
  }
  return [...items, { id: nid(), kind: 'assistant', text, streaming: true }]
}

// 结束所有流式中的 assistant 气泡。轮次边界（complete/error）必须调用：
// 残留的 streaming 气泡会被下一轮 appendDelta 匹配，新回复拼进旧气泡。
function finishStreaming(items: Item[]): Item[] {
  return items.map((it) =>
    it.kind === 'assistant' && it.streaming ? { ...it, streaming: false } : it,
  )
}

// 子代理内部事件归属：把 tool.start/complete 与 token 用量写进 runId 匹配的 agent 卡片。
// 找不到父卡片（嵌套子代理等）则返回 null，由调用方丢弃。
// 子事件 payload：tool.start / tool.complete / message.complete 三者的字段并集（按访问取并、全可选），
// 由 type 在运行时区分分支，故类型层不需判别——保留宽松形状即可覆盖三种。
type ChildEventPayload = Partial<
  WireEventPayloads['tool.start'] &
    WireEventPayloads['tool.complete'] &
    WireEventPayloads['message.complete']
>

function applyChildEvent(
  s: SessionState,
  parentRun: string,
  type: string,
  payload: ChildEventPayload,
): SessionState | null {
  // 从尾部反向找：agent 卡片几乎总在对话流末尾，长会话下避免每个子事件全量正扫
  let idx = -1
  for (let i = s.items.length - 1; i >= 0; i--) {
    const it = s.items[i]
    if (it.kind === 'tool' && it.runId === parentRun) {
      idx = i
      break
    }
  }
  if (idx < 0) return null
  const agent = s.items[idx] as ToolItem
  const children = agent.children ?? []
  let next: ToolItem
  if (type === 'tool.start') {
    const tcid = payload.tool_call_id ?? ''
    if (tcid && children.some((c) => c.toolCallId === tcid)) return null
    next = {
      ...agent,
      children: [...children, { toolCallId: tcid, name: payload.name ?? '', args: payload.args, done: false }],
    }
  } else if (type === 'tool.complete') {
    next = {
      ...agent,
      children: children.map((c) =>
        c.toolCallId === payload.tool_call_id ? { ...c, done: true, error: !!payload.is_error } : c,
      ),
    }
  } else if (type === 'message.complete' && payload.usage) {
    // usage 按 max 累计（与 TUI agent_group.record_tokens 同口径）
    next = {
      ...agent,
      inTok: Math.max(agent.inTok ?? 0, payload.usage.input_tokens ?? 0),
      outTok: Math.max(agent.outTok ?? 0, payload.usage.output_tokens ?? 0),
    }
  } else {
    return null
  }
  const items = s.items.slice()
  items[idx] = next
  return { ...s, items }
}

// 每个会话的独立状态（多会话并发：A 在跑时可切到 B，互不影响）
type SessionState = {
  items: Item[]
  running: boolean
  // 当前进行中的思考流文本（只在思考期间非空；正文/工具一开始即清空，不留痕迹）
  thinkingText: string
  // 挂起的审批/澄清按 approval_id 排队（后端并发解锁：一条消息多个工具 / 多个前台子代理
  // 可同时挂起审批）；渲染队首，逐个应答出队，不丢任何挂起的 Future。
  approval: Record<string, unknown>[]
  clarify: Record<string, unknown>[]
  // 最近一次模型调用的上下文用量（用于输入栏的上下文进度环）；首轮前为 undefined
  ctx?: CtxUsage
  // 渠道旁观会话的上下文环分母：会话真实模型名与其窗口（desktop 无本地 activeModel 可依，
  // 由 load_history 快照带出）；desktop 自己的会话不用（直接取 activeModel）
  ctxModel?: string
  ctxWindow?: number
  // 历史压缩进行中（Summarizer 内部摘要调用期间为 true）；展示「正在压缩对话」指示
  compacting?: boolean
}
const emptySession = (items: Item[] = []): SessionState => ({
  items,
  running: false,
  thinkingText: '',
  approval: [],
  clarify: [],
})

// 用某机器的最新 bg 任务快照替换该机器那一段（保留其它机器的），并给每条打上 backend 标记。
// bg_tasks.update / list_bg_tasks 是各机器进程级快照（仅含本机任务），直接整列 setBgTasks 会
// 抹掉别机的任务，故按机器分段替换——同一飞书群 thread 在多台机器上会重名，靠 backend 区分。
const replaceBackendTasks = (prev: BgTask[], backend: string, tasks: BgTask[]): BgTask[] => [
  ...prev.filter((t) => beOf(t) !== backend),
  ...tasks.map((t) => ({ ...t, backend })),
]

// 进程级广播事件：与具体会话无关，handleEvent 在 session 路由之前统一处理。
// 远程机器通常没有活跃会话连接、只有一条控制连接，故控制连接也要把这些转给
// handleEvent——否则远程的定时任务/后台任务在界面上永远是静止的。本机会经两条
// 连接各收一次，这些处理器都按机器整段覆盖或自带去重，重复到达幂等。
const PROCESS_EVENTS = new Set(['cron.result', 'cron.running', 'cron.jobs', 'bg_tasks.update', 'mcp.status'])

// 按 approval_id 把挂起的审批/澄清入队；已在队列则原样返回（重连后端会重发，去重保幂等）。
const enqueuePending = (
  queue: Record<string, unknown>[],
  item: Record<string, unknown>,
): Record<string, unknown>[] => {
  const id = (item as { approval_id?: string }).approval_id
  return queue.some((p) => (p as { approval_id?: string }).approval_id === id)
    ? queue
    : [...queue, item]
}

// 从 LangChain usage_metadata 提炼上下文环所需快照。input_tokens 含缓存命中部分，
// 直接作为「当前上下文占用」；缺字段（如非流式补发不带 input_tokens）返回 undefined。
const ctxFromUsage = (u: Usage | undefined): CtxUsage | undefined => {
  if (!u || typeof u.input_tokens !== 'number') return undefined
  return {
    used: u.input_tokens,
    output: u.output_tokens ?? 0,
    cacheRead: u.input_token_details?.cache_read ?? 0,
  }
}

// 会话是否有流式在途的 assistant 气泡：历史快照能否整表替换的判据。
const hasStreaming = (s: SessionState): boolean =>
  s.items.some((it) => it.kind === 'assistant' && it.streaming)

// loadHistory 结果 → 会话槽位的统一水合（初次加载 / 重连补拉 / 渠道切回三处共用）。
// 已有流式在途内容时保留现有 items 不覆盖：checkpoint 快照比正在流出的直播轮旧，
// 整体替换会截断刚流入的助手内容/工具卡。调用方置 loaded 前须自查 hasStreaming——
// 快照被丢弃时置 loaded 会把掉线前的历史永久关在补拉门外。
function hydrateHistory(
  s: SessionState,
  r: { items: HistoryItem[]; usage?: Usage; model?: string; context_window?: number },
): SessionState {
  return {
    ...s,
    items: hasStreaming(s) ? s.items : r.items.map(restore),
    ctx: ctxFromUsage(r.usage) ?? s.ctx,
    // 渠道旁观会话的上下文环分母来源（会话真实模型窗口）；desktop 自己的会话此值虽也回填但不消费。
    // 模型名与窗口成对更新：窗口未知（0，如目录查不到的模型）时整对保旧，避免明细弹窗
    // 出现「新模型名 · 旧模型窗口」的错配。
    ...(r.context_window ? { ctxModel: r.model, ctxWindow: r.context_window } : {}),
  }
}

export default function App() {
  const [store, setStore] = useState<Record<string, SessionState>>({})
  const [active, setActive] = useState('')
  const [conn, setConn] = useState<ConnState>('connecting')
  const [model, setModel] = useState('')
  // 进程级工作目录 = 当前项目（gateway.ready 下发；切项目对整个 app 生效）
  const [workspaceDir, setWorkspaceDir] = useState('')
  // handleEvent（稳定 useCallback）里做 mcp.status 的当前工作区过滤，须经 ref 读最新值
  const workspaceDirRef = useRef('')
  useEffect(() => {
    workspaceDirRef.current = workspaceDir
  }, [workspaceDir])
  const [projects, setProjects] = useState<Project[]>([])
  // 项目视图作用的机器（方案甲「先选机器」）+ 该机器当前项目
  const [projectsMachine, setProjectsMachine] = useState('local')
  const [projectsCurrent, setProjectsCurrent] = useState('')
  // 项目页是被「新建会话」阻断跳转到的（而非用户主动点「项目」标签）时，顶部提示为什么在这里
  const [needProjectHint, setNeedProjectHint] = useState(false)
  const [showNewProject, setShowNewProject] = useState(false)
  const [addingFolder, setAddingFolder] = useState(false) // 添加可访问目录的浏览器开关
  const [pendingRemoveProject, setPendingRemoveProject] = useState<Project | null>(null)
  // 各会话临时添加的额外可访问目录（连接级状态的前端镜像）
  const [folderStore, setFolderStore] = useState<Record<string, string[]>>({})
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
  const [machines, setMachines] = useState<{ id: string; name: string; enabled?: boolean }[]>([
    { id: 'local', name: '本地' },
  ])
  const [machineConn, setMachineConn] = useState<Record<string, ConnState>>({})
  const [providers, setProviders] = useState<ProviderProfile[]>([])
  const [activeModel, setActiveModel] = useState<ActiveModel>({ provider: '', model: '' })
  // auto 审批分类器指针（providers.json 顶级 classifier，空=跟随会话模型）
  const [classifier, setClassifier] = useState<ModelPointer>({})
  // 工具审批模式：随后续 send/run 透传给后端（auto=AI 审批分类器）
  const [toolMode, setToolMode] = useState<ToolMode>('default')
  // 活动会话所在机器：ModelPicker 机器标识 + 设置改模型时判断是否需刷新聊天侧。
  // 从复合 active key 派生（机器 id 已编码其中），杜绝与 active 脱同步——任何切换路径
  // （activate / 通知点击等）只要 setActive 就自动带对机器。空/无分隔符归一到 'local'。
  const activeBackend = keyBackend(active) || 'local'
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
  // 「最近」列表最多显示条数（界面偏好，localStorage 记忆，默认 20）
  const [recentLimit, setRecentLimit] = useState(() => {
    const v = parseInt(localStorage.getItem('lumi-recent-limit') || '20', 10)
    return Number.isFinite(v) ? v : 20
  })
  const changeRecentLimit = (n: number) => {
    localStorage.setItem('lumi-recent-limit', String(n))
    setRecentLimit(n)
  }
  // 图片嵌入消息（dataUrl→image 块）；其它文件只带绝对路径，发送时写进消息文本，
  // 由 Agent 用工具读取（不在此预授权，交给现有权限流程）
  const [attachments, setAttachments] = useState<Attachment[]>([])
  // 主区视图：聊天 / 项目管理页 / 定时任务管理页 / 任务会话视图（某任务的某次执行对话 + Runs 侧栏）
  const [bgTasks, setBgTasks] = useState<BgTask[]>([]) // 后台任务全量快照（按 thread 过滤展示）
  const [preview, setPreview] = useState<PresentedFile | null>(null) // present_files 右侧预览面板（null=关）
  // 可拖拽边栏宽度（持久化）：左侧会话栏 + 统一右栏 + 文件预览
  const sidebarW = useResizableWidth('lumi-sidebar-width', 256, 200, 420)
  // 悬浮侧栏展开/收起（持久化）
  const [sidebarOpen, toggleSidebar] = usePersistedFlag('lumi-sidebar-open')
  // 统一右栏（执行记录/后台任务模块）开合：chat 与 cronjob 视图共用一份状态与宽度，
  // 收起在哪个视图都保持收起——对用户而言它是同一个部件
  const [railOpen, toggleRail] = usePersistedFlag('lumi-rail-open')
  // 与左侧栏同参（默认 256、拖拽 200-420），左右观感对称；键名带 v2 让早期 320 的
  // 旧存值失效回默认（旧键 lumi-rail-width 未发布过，只存在于开发机）
  const railW = useResizableWidth('lumi-rail-width-v2', 256, 200, 420)
  const previewW = useResizableWidth('lumi-preview-width', 520, 360, 920)
  const [view, setView] = useState<'chat' | 'projects' | 'project' | 'scheduled' | 'cronjob'>('chat')
  // 项目主页当前查看的项目（view='project' 时有效）
  const [projectHome, setProjectHome] = useState<{ backend: string; path: string } | null>(null)
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
  const viewRef = useRef(view)
  const activeCronJobRef = useRef(activeCronJob)
  // cron 事件经 DesktopDelivery 广播到每条 WS 连接，多会话时同一结果会收到多次，按 key 去重
  const seenCronRef = useRef(new Set<string>())
  const fileInputRef = useRef<HTMLInputElement>(null)
  const connsRef = useRef<Record<string, Gateway>>({})
  const activeRef = useRef('')
  // activate 调用序号：判废晚到的建连结果（openConnection 悬置期间用户切走后不回拽）
  const activationSeqRef = useRef(0)
  // 会话列表镜像到 ref：切会话时据此查它所属项目（workspace_dir），随 switch 下发让后端切 cwd
  const sessionsRef = useRef<SessionMeta[]>([])
  // 本窗口手动命名过的会话 key：session.title 广播与手动重命名竞态时，
  // 自动标题不得覆盖用户敲的名字（后端侧手动名本就优先，只是事件可能晚到）。
  // 按数据标记而非 RPC 在途时序——广播与 rename 响应帧之间没有顺序保证。
  const renamedRef = useRef<Set<string>>(new Set())
  // 每台机器一条「控制连接」：用于跨机器 fan-out list_sessions / 全局 RPC（非 chat 流）
  const controlConns = useRef<Record<string, Gateway>>({})
  const cronJobsRef = useRef<CronJob[]>([]) // 据此把定时操作路由到任务所属机器
  const refreshCronJobsRef = useRef<((only?: string) => void) | null>(null) // handleEvent 经此按机器刷新
  const scrollRef = useRef<HTMLDivElement>(null)
  // 聊天流「粘底」：贴底时才跟随流式输出，用户上滚即放手（不再抢界面）。
  // pinnedRef 供自动滚动 effect 同步判定；showJump 驱动「回到底部」浮钮渲染。
  const pinnedRef = useRef(true)
  const [showJump, setShowJump] = useState(false)
  const lastActiveRef = useRef(active) // 与当前 active 不同即说明刚切了会话
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // handleEvent 是 []-依赖的稳定回调，通过 ref 读取最新的 store / 通知开关 / 翻译
  const storeRef = useRef<Record<string, SessionState>>({})
  const notifyRef = useRef(notify)
  const tRef = useRef(t)
  // MCP 失败 toast 去重（`backend:server:error` → 上次弹出时刻）：配置保存→作废→
  // 重载的连环加载会重播同一失败，60s 内不重复弹
  const mcpToastAtRef = useRef(new Map<string, number>())
  const cronRefreshAtRef = useRef(new Map<string, number>()) // cron.jobs 本机经两条连接各来一次，按机器去重
  // 面板刷新信号合并：同一池的广播每条绑定连接各收一帧，微任务尾只发一次
  const mcpSignalQueuedRef = useRef(false)
  // 临时目录是连接级（bridge 内存）状态：重连得到全新 bridge 后需重放，故镜像到 ref
  const folderStoreRef = useRef<Record<string, string[]>>({})

  useEffect(() => {
    folderStoreRef.current = folderStore
  }, [folderStore])
  useEffect(() => {
    activeRef.current = active
  }, [active])
  useEffect(() => {
    sessionsRef.current = sessions
  }, [sessions])
  useEffect(() => {
    cronJobsRef.current = cronJobs
  }, [cronJobs])
  useEffect(() => {
    viewRef.current = view
  }, [view])
  useEffect(() => {
    activeCronJobRef.current = activeCronJob
  }, [activeCronJob])
  useEffect(() => {
    localStorage.setItem('lumi-cron-read-runs', JSON.stringify(readRuns))
  }, [readRuns])

  // 开启通知：持久化 + 立即发一条测试通知验证（经主进程，见 preload.notify）。
  const toggleNotify = (v: boolean) => {
    setNotify(v)
    localStorage.setItem('lumi-notify', v ? '1' : '0')
    if (v) void window.lumi.notify?.({ title: 'Lumi', body: t('notify.enabled') })
  }

  // 通知点击：主进程已聚焦窗口，这里切到对应会话
  useEffect(() => {
    window.lumi.onNotifyClick?.((tag) => {
      if (tag) setActive(tag)
    })
  }, [])

  // 当前活动会话的派生视图
  const cur = store[active]
  const items = cur?.items ?? []
  const running = cur?.running ?? false
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
  // 当前 active 模型的上下文窗口（tokens）；能力未知时为 0，ContextMeter 自会隐藏。
  // memo 化避免每次流式 token 重渲染都重跑 providers.find。
  const contextWindow = useMemo(
    () => providers.find((p) => p.id === activeModel.provider)?.context?.[activeModel.model] ?? 0,
    [providers, activeModel.provider, activeModel.model],
  )

  useEffect(() => {
    storeRef.current = store
  }, [store])
  useEffect(() => {
    notifyRef.current = notify
  }, [notify])
  useEffect(() => {
    tRef.current = t
  })

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
      // 未读不在此累积：重拉任务列表即带回最新 run_threads，角标随之派生（见 cronVersion effect）
      setCronVersion((v) => v + 1)
      const viewingThisJob =
        viewRef.current === 'cronjob' && activeCronJobRef.current === payload.job_id
      // 正在看该任务的会话视图时，新执行直接记为已读——否则角标会在用户眼皮底下
      // 跳出「1 new」。派生模式下「不算未读」的等价表达就是标已读
      if (viewingThisJob && payload.thread_id) {
        setReadRuns((r) => (r[payload.thread_id] ? r : { ...r, [payload.thread_id]: true }))
      }
      // 正在看该任务且窗口聚焦时不打扰，其余情况按通知开关弹系统通知
      if (notifyRef.current && (!viewingThisJob || !document.hasFocus())) {
        const t = tRef.current
        void window.lumi.notify?.({
          title: payload.status === 'success' ? t('notify.cronDone') : t('notify.cronFailed'),
          body: `${payload.job_name}: ${String(payload.output ?? '').slice(0, 80)}`,
        })
      }
      return
    }
    const sid = sessionKey(backend, ev.session_id ?? '')
    const parentRun: string = payload.parent_run_id ?? ''
    // 子代理的逐字流（正文/思考）不进 UI——只把子工具调用与 token 用量归属到父
    // agent 卡片（见下方 applyChildEvent）。中断类（审批/澄清/计划）即便带
    // parent_run_id 也照常往下走，仍需用户处理。
    if (parentRun && (type === 'message.delta' || type === 'message.start' || type === 'thinking.delta')) {
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
    setStore((store) => {
      const s = store[sid]
      if (!s) return store
      let n: SessionState | null = null
      // 子代理的工具调用与 token 用量归属到父 agent 卡片，不进主流；其余带 parent_run_id
      // 的事件（审批/澄清/计划/错误/轮次完成等中断）仍需用户处理，照常走下方 switch。
      if (parentRun && (type === 'tool.start' || type === 'tool.complete' || type === 'message.complete')) {
        return { ...store, [sid]: applyChildEvent(s, parentRun, type, payload) ?? s }
      }
      switch (type) {
        // message.start 不再预建空 assistant：模型直接调工具（无文字）时会留下空气泡，
        // 还会把相邻工具在 groupItems 里隔断。改由首个 message.delta 懒创建气泡。
        case 'message.delta':
          n = { ...s, items: appendDelta(s.items, payload.text ?? '') }
          break
        case 'thinking.delta':
          n = { ...s, thinkingText: s.thinkingText + (payload.text ?? '') }
          break
        case 'compaction.status':
          // 历史压缩进行中：仅切状态，不进消息流（摘要全文由后端拦截，不会泄漏为助手回答）
          n = { ...s, compacting: !!payload.active }
          break
        case 'message.complete':
          n = { ...s, items: finishStreaming(s.items), ctx: ctxFromUsage(payload.usage) ?? s.ctx }
          break
        case 'tool.start': {
          const tcid = payload.tool_call_id ?? ''
          // 防御性去重：同一 tool_call_id 重复 tool.start 只建一行（在途审批后 ask 单次发出，
          // 此守卫不再为 ask 必需，仅兜底任何意外重发）
          if (tcid && s.items.some((it) => it.kind === 'tool' && it.toolCallId === tcid)) break
          n = {
            ...s,
            items: [
              ...s.items,
              {
                id: nid(),
                kind: 'tool',
                toolCallId: tcid,
                name: payload.name ?? '',
                args: payload.args,
                output: '',
                done: false,
                // agent 工具自带 run_id：子工具事件经 parent_run_id 归属到此卡片
                ...(payload.run_id ? { runId: payload.run_id, children: [] } : {}),
              },
            ],
          }
          break
        }
        case 'tool.complete':
          n = {
            ...s,
            items: s.items.map((it) =>
              it.kind === 'tool' && it.toolCallId === payload.tool_call_id
                ? { ...it, output: payload.output ?? '', done: true, error: !!payload.is_error }
                : it,
            ),
          }
          break
        case 'approval.request': {
          // 追加而非覆盖：并发审批各自入队、逐个处理，不丢任何挂起的 Future（去重见 enqueuePending）
          const q = enqueuePending(s.approval, payload)
          n = q === s.approval ? s : { ...s, approval: q }
          break
        }
        case 'clarify.request': {
          const q = enqueuePending(s.clarify, payload)
          n = q === s.clarify ? s : { ...s, clarify: q }
          break
        }
        case 'turn.complete':
          // 轮结束：清掉可能残留的审批/澄清对话框（如 stop/切会话把挂起审批以拒绝收尾，
          // 此时不经 decide/resume 清理，靠 turn.complete 兜底关闭弹窗）
          n = {
            ...s,
            running: false,
            compacting: false,
            approval: [],
            clarify: [],
            items: finishStreaming(s.items),
            ctx: ctxFromUsage(payload.usage) ?? s.ctx,
          }
          break
        case 'error':
          // 出错中断的流（bridge 只发 error、无 message.complete）也要收尾气泡 + 关弹窗
          n = {
            ...s,
            running: false,
            compacting: false,
            approval: [],
            clarify: [],
            items: [...finishStreaming(s.items), { id: nid(), kind: 'notice', text: payload.message }],
          }
          break
      }
      // 思考的生命周期统一收口：除 thinking.delta 自身外，任何事件到达都意味着
      // 这段思考已结束（正文/工具/审批/轮次边界），清空累积文本——新增事件类型
      // 无需再各自记得清理
      if (n && type !== 'thinking.delta' && n.thinkingText) {
        n = { ...n, thinkingText: '' }
      }
      return n ? { ...store, [sid]: n } : store
    })
  }, [])

  // 为某会话建立一条独立 WS 连接（每会话一条，互不阻塞）。targetThread=null 为新会话。
  const openConnection = useCallback(
    (targetThread: string | null, targetWorkspace = '', backendId = 'local'): Promise<string> => {
      return new Promise<string>((resolve) => {
        void (async () => {
          const { wsUrl } = await window.lumi.getConnection(backendId)
          // open 握手携带 workspace：后端据此把本会话引擎直接 pin 到该项目（项目随会话
          // 绑定，不动进程 cwd），省掉 ready 后再 switch 改项目的来回。重连复用同一 URL，
          // 故新 bridge 也会被重新 pin。
          // new URL 对非法地址会抛（远程机器 URL 仅经弱校验入库）；catch 后退回原始串交给
          // WebSocket 层，其 onclose→failed 优雅降级，避免抛进本 IIFE 致 Promise 永不 resolve。
          let connectUrl = wsUrl
          try {
            const u = new URL(wsUrl)
            if (targetWorkspace) u.searchParams.set('workspace', targetWorkspace)
            // 已有会话：初次连接（含 Ctrl+R 重载后点回会话）即携带 ?thread=，让后端在建空
            // bridge 前先认领回断连期仍挂着的会话（parked 审批/运行轮原样还在）。新会话无
            // thread（握手分配后由 bindThread 补给后续重连）。
            if (targetThread) u.searchParams.set('thread', targetThread)
            connectUrl = u.toString()
          } catch {
            /* 非法 URL：保留原始串，连接失败由 Gateway 的重连/failed 态呈现 */
          }
          const gw = new Gateway(connectUrl)
          // 本连接所属会话的复合 key（backend+thread）——store/connsRef/folderStore 皆以此为键；
          // 发给后端的 wire 仍用裸 thread（myThread）。
          const targetKey = targetThread ? sessionKey(backendId, targetThread) : ''
          // 创建即登记（不等 ready）：快速连点同一会话时 activate 的复用分支能立即命中，
          // 不会开出重复连接；boot 清理也能收殓在途连接。死连接由 activate 驱逐后重建。
          if (targetKey) connsRef.current[targetKey] = gw
          let myThread = ''
          let myKey = ''
          // 本连接所属项目；重连得到全新 bridge 后据此切回原 thread + 重放临时目录
          let myWorkspace = ''
          let ready = false
          // 历史是否已成功加载并应用：初次加载被瞬断打断、或快照因流式在途被
          // hydrateHistory 丢弃时保持 false，重连 ready / 轮次收尾时补拉
          let loaded = false
          // 补拉在途标记：防抖动连接下多个 ready 并发重复 loadHistory
          let loadingHistory = false
          // 历史快照统一落位（初次加载 / 补拉共用）：无流式在途 = hydrateHistory
          // 会真正应用快照，此时才算加载完成——被丢弃时置 loaded 会把掉线前的
          // 历史永久关在补拉门外
          const applySnapshot = (
            key: string,
            r: { items: HistoryItem[]; usage?: Usage; model?: string; context_window?: number },
            patch: Partial<SessionState> = {},
          ) => {
            setStore((s) => {
              const cur = s[key]
              if (!cur) return s
              if (!hasStreaming(cur)) loaded = true
              return { ...s, [key]: { ...hydrateHistory(cur, r), ...patch } }
            })
          }
          const backfillHistory = () => {
            if (loaded || loadingHistory || !myThread) return
            loadingHistory = true
            void gw
              .loadHistory(myThread)
              .then((r) => applySnapshot(myKey, r))
              .catch(() => {})
              .finally(() => {
                loadingHistory = false
              })
          }
          gw.onEvent((ev) => {
            if (ev.type === 'gateway.ready') {
              setModel((m) => m || ev.payload.model || '')
              // workspace_bound=false 时 payload.workspace 只是进程 cwd 兜底展示值，不是真
              // 绑定的项目——写进 workspaceDir 会污染侧栏项目分组等展示，未绑定就不写。
              if (ev.payload.workspace_bound && ev.payload.workspace) {
                setWorkspaceDir(ev.payload.workspace)
              }
              if (ready) {
                // 重连：服务端给的是全新 bridge（新 session_id），切回本连接原 thread
                // 恢复后端绑定，否则会丢弃原会话、并多出一个幽灵空会话。
                // 新 bridge 的临时目录为空，需重放本会话已添加的目录，否则徽标显示
                // 有目录而后端实际访问不到。
                if (myThread) {
                  void gw.switchSession(myThread, myWorkspace)
                  for (const f of folderStoreRef.current[myKey] ?? []) {
                    void gw.addFolder(f)
                  }
                  // 初次历史加载被瞬断打断：重连后补拉（真正应用才置 loaded，失败留给下次触发）
                  backfillHistory()
                  // 复位运行态：断连时 sendMessage 的 catch 已 resetRunning(false)，续接后
                  // 据后端实际运行态恢复 running（否则挂起轮被当空闲，stop 隐藏/输入栏启用）。
                  setStore((s) =>
                    s[myKey]
                      ? { ...s, [myKey]: { ...s[myKey], running: !!ev.payload.running } }
                      : s,
                  )
                }
                return
              }
              ready = true
              // 初始拉取后台任务快照（之后变更经 bg_tasks.update 推送）；按本机分段替换
              void gw
                .listBgTasks()
                .then((r) => setBgTasks((prev) => replaceBackendTasks(prev, backendId, r.tasks)))
                .catch(() => {})
              if (targetThread) {
                // 已有会话：切到该 thread 并加载历史。switchSession 可能因项目目录已被删/改名
                // 而被后端拒（set_workspace 抛错）；必须吞掉，否则 Promise 永不 resolve、会话卡死。
                void (async () => {
                  // 先占位 store：使续接重发的审批卡有处可落（否则在 loadHistory
                  // 完成前到达会被事件处理器的 `if (!s) return` 丢弃）。
                  setStore((s) => ({ ...s, [targetKey]: s[targetKey] ?? emptySession() }))
                  // 身份先行：下方 await 可能因瞬断 reject（sidecar 重启窗口），若身份
                  // 未落，重连分支认不回本会话、resolve 永不调用，会话将永久空白。
                  myThread = targetThread
                  myKey = targetKey
                  myWorkspace = targetWorkspace
                  gw.bindThread(targetThread)
                  try {
                    await gw.switchSession(targetThread, targetWorkspace)
                  } catch {
                    /* 目录失效等：仍打开会话（后端已降级，不切 cwd） */
                  }
                  try {
                    const r = await gw.loadHistory(targetThread)
                    // 引擎已在 open 握手 pin 到本会话项目；同步当前项目指示
                    if (targetWorkspace) setWorkspaceDir(targetWorkspace)
                    // 水合历史（保留续接期间已落入的审批/澄清队列与流式在途内容）；
                    // running 据后端运行态恢复（续接的挂起轮要显示运行态，否则被当空闲）。
                    applySnapshot(targetKey, r, { running: !!ev.payload.running })
                  } catch {
                    /* 瞬断等：历史未加载（loaded=false），重连 ready 分支补拉 */
                  }
                  // UI 已乐观切到本会话，连接问题由指示灯呈现；无论成败都 resolve，
                  // 不让 activate 悬置
                  resolve(targetKey)
                })()
              } else {
                // 新会话：用握手分配的 thread；记录其所在项目（open 已 pin，握手下发的
                // workspace 即本会话项目），供重连时切回原 thread。
                myThread = ev.session_id ?? ''
                myKey = sessionKey(backendId, myThread)
                myWorkspace = ev.payload.workspace || ''
                loaded = true // 新会话无历史可拉，重连分支不做补拉覆盖
                gw.bindThread(myThread) // 重连携带 ?thread= → 后端断连续接
                connsRef.current[myKey] = gw
                setStore((s) => ({ ...s, [myKey]: emptySession() }))
                resolve(myKey)
              }
            } else {
              handleEvent(ev, backendId)
              // 补拉曾因流式在途被丢弃（loaded 仍 false）：轮次收尾后流式气泡已收，
              // 快照可安全整表替换，此时重试把掉线前的历史找回来
              if ((ev.type === 'turn.complete' || ev.type === 'error') && !loaded) {
                backfillHistory()
              }
            }
          })
          gw.onState((st) => {
            if (myKey && myKey === activeRef.current) setConn(st)
          })
          gw.connect()
        })()
      })
    },
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
  }, [setPinned])

  const jumpToBottom = useCallback(() => {
    setPinned(true)
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [setPinned])

  useEffect(() => {
    if (conn === 'open' && !running) inputRef.current?.focus()
  }, [conn, running, active])

  // 会话管理 / cron RPC 操作全局资源，与连接当前 thread 无关，任一活跃连接皆可。
  // 稳定引用（useCallback []）：作为 CronPage 的 api prop，避免每次渲染触发其刷新。
  // 全局 RPC（providers/cron/projects/bg）走本地控制连接（这些当前以本地机器为准；
  // 远程的同类作用域属层3）。控制连接缺位时回退到任一会话连接。
  const anyGw = useCallback(
    () =>
      controlConns.current['local'] ??
      Object.values(controlConns.current)[0] ??
      connsRef.current[activeRef.current] ??
      Object.values(connsRef.current)[0],
    [],
  )
  // 某台机器的控制连接（pin/重命名/删除等按会话所属机器路由）
  const gwForBackend = useCallback(
    (backend: string) => controlConns.current[backend] ?? anyGw(),
    [anyGw],
  )

  // 后台任务属于当前会话的 bridge，必须发到该会话的连接（不是控制连接 anyGw，
  // 否则后端按控制连接的 thread_id 匹配不到，停/清都是空操作、任务还会回来）。
  // 乐观更新只作用于当前会话所在机器的任务：task_id 可能跨机重名，按 active 的 backend 圈定
  const stopBgTask = useCallback((taskId: string) => {
    const be = keyBackend(activeRef.current)
    setBgTasks((ts) =>
      ts.map((x) => (x.task_id === taskId && beOf(x) === be ? { ...x, status: 'failed' as const } : x)),
    )
    void connsRef.current[activeRef.current]?.stopBgTask(taskId).catch(() => {})
  }, [])

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
  const hasRunningBg = activeBgTasks.some((tk) => tk.status === 'running')
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

  // 重拉某会话历史并整表替换其 items（渠道会话旁观刷新 / 切回对账共用）
  const reloadHistory = useCallback((key: string, threadId: string) => {
    connsRef.current[key]
      ?.loadHistory(threadId)
      .then((r) =>
        setStore((s) => (s[key] ? { ...s, [key]: hydrateHistory(s[key], r) } : s)),
      )
      .catch(() => {})
  }, [])

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

  // 重连某机器：重新从配置取最新地址（设置里可能改过 url/token）再 reconnect，
  // 否则 Gateway 仍持有构造时的旧地址。启用的机器其连接恒在 controlConns 里（失败态不删），
  // 走到无连接分支的只有已禁用/已删机器——这些不该建连（建连由 syncBackends 按 enabled 决定）。
  const reconnectMachine = useCallback(async (backend: string) => {
    const gw = controlConns.current[backend]
    if (!gw) return
    const { wsUrl } = await window.lumi.getConnection(backend)
    gw.setUrl(wsUrl)
    gw.reconnect()
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
    (backend: string) => {
      const existing = controlConns.current[backend]
      if (existing) {
        // 已有连接：健康则幂等返回；已停摆（failed/被关闭）则换最新地址复活——
        // 否则远程瞬断进 failed 后会永久滞留，syncBackends 命中本守卫也唤不醒它。
        if (existing.dead) void reconnectMachine(backend)
        return
      }
      void (async () => {
        const { wsUrl } = await window.lumi.getConnection(backend)
        const gw = new Gateway(wsUrl)
        gw.onEvent((ev) => {
          if (ev.type === 'gateway.ready') {
            void refreshSessions()
            refreshCronJobs()
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
          setMachineConn((m) => ({ ...m, [backend]: st }))
          if (st !== 'open') clearMachineSnapshots(backend)
        })
        controlConns.current[backend] = gw
        gw.connect()
      })()
    },
    [refreshSessions, refreshCronJobs, refreshChannels, reloadHistory, reconnectMachine, patchSession, handleEvent, clearMachineSnapshots],
  )

  // 同步机器表 → 开新机器的控制连接、关掉已删机器的，刷新会话。BackendsPanel 增删后回调此。
  const syncBackends = useCallback(async () => {
    const data = await window.lumi.backends?.list()
    // 全量（含已禁用）入 machines：machineColor 按数组下标取色，删项会让其余机器串色
    const list = [
      { id: 'local', name: '本地', enabled: true },
      ...(data?.remotes ?? []).map((r) => ({ id: r.id, name: r.name || r.url, enabled: r.enabled !== false })),
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
        setChannels((c) => {
          const next = { ...c }
          delete next[id]
          return next
        })
        clearMachineSnapshots(id)
      }
    }
    for (const id of wanted) openControlConn(id)
    void refreshSessions()
  }, [openControlConn, refreshSessions, clearMachineSnapshots])

  // 查该机器登记的默认项目路径，顺带把 projects/projectsCurrent 同步成最新——boot effect
  // 与下方 goNewChat 共用同一份查找逻辑，避免各自倒腾一遍 listProjects。
  // 返回 null = 没查到（连接波动等），空串 = 查到了但确实没有默认项目，两者调用方处理不同
  // （前者该重试，后者不用）。声明在 boot effect 之前，纯是为了这条 useEffect 能引用它。
  const fetchDefaultProject = useCallback(
    async (backend: string): Promise<string | null> => {
      try {
        const r = await gwForBackend(backend)?.listProjects()
        if (!r) return null
        setProjects(r.projects)
        setProjectsCurrent(r.current)
        return r.projects.find((p) => p.default)?.path ?? ''
      } catch {
        return null
      }
    },
    [gwForBackend],
  )

  // 初始：起各机器控制连接（合并会话列表）+ 本地一条新会话连接（聊天流）。
  // 聊天必须绑定项目——开局不能像从前那样无条件开一条 workspace='' 的空会话：
  // 有默认项目就直接绑上，没有则打开后立刻转去项目选择器提示，不放行未绑定输入。
  useEffect(() => {
    let disposed = false
    void (async () => {
      await syncBackends()
      const workspace = (await fetchDefaultProject('local')) || ''
      const tid = await openConnection(null, workspace, 'local')
      if (!disposed) {
        setActive(tid)
        setConn('open')
        if (!workspace) {
          setNeedProjectHint(true)
          setView('projects')
        }
      }
    })()
    return () => {
      disposed = true
      Object.values(connsRef.current).forEach((g) => g.close())
      Object.values(controlConns.current).forEach((g) => g.close())
      // 必须清空 ref：close() 置 closedByUser=true 使其永不重连，若残留在 ref 里，
      // effect 重跑（HMR / 依赖变更）时 openControlConn 的幂等短路(if conns[id] return)
      // 会命中这些死连接而跳过重建 → 本地+远程控制连接全断、会话列表灰掉，只能整页重载。
      connsRef.current = {}
      controlConns.current = {}
    }
  }, [openConnection, syncBackends, fetchDefaultProject])

  // 兜底自愈：窗口重获焦点时全量刷新会话列表。ready 首拉失败后没有别的自动重试路径，
  // 没有这条，空列表会一直定格到手动重载（服务端有 checkpoint_id 缓存，刷新很便宜）。
  useEffect(() => {
    const onFocus = () => void refreshSessions()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [refreshSessions])

  // BackendsPanel 增删/编辑远程机器后广播此事件 → 重连各机器、刷新合并列表（无 reload）。
  // detail.reconnectId：编辑了某机器的地址/token，需对该机器换址重连（syncBackends 幂等不会重建）。
  useEffect(() => {
    const onChanged = (e: Event) => {
      void syncBackends()
      const id = (e as CustomEvent<{ reconnectId?: string }>).detail?.reconnectId
      if (id) void reconnectMachine(id)
    }
    window.addEventListener('lumi:backends-changed', onChanged)
    return () => window.removeEventListener('lumi:backends-changed', onChanged)
  }, [syncBackends, reconnectMachine])

  // 按机器拉项目（方案甲先选机器）：projects + 该机器当前项目（projectsCurrent）。
  // 活动会话的 workspaceDir 由 activate/gateway.ready 维护，与项目视图分离。
  const refreshProjects = useCallback(
    async (backend = 'local') => {
      try {
        const r = await gwForBackend(backend)?.listProjects()
        if (r) {
          setProjects(r.projects)
          setProjectsCurrent(r.current)
        }
      } catch {
        /* 忽略：连接波动时静默 */
      }
    },
    [gwForBackend],
  )

  // 只在回合结束（running 落回 false）和切会话时刷新：发送时刷新没有新信息
  // （首条消息尚未落 checkpoint），白白多一次全量 checkpoint 扫描。
  useEffect(() => {
    if (active && !running) void refreshSessions()
  }, [active, running, refreshSessions])

  // 拉取斜杠命令（技能命令，按项目动态）。技能目录随项目变化，故进入命令模式时刷新。
  // 斜杠命令来自当前会话所在机器（命令在会话连接上执行）——远程会话用远程的 skills，
  // 否则菜单/校验是本地命令、发远程独有命令会被判非法。
  const loadCommands = useCallback(() => {
    connsRef.current[activeRef.current]
      ?.listCommands()
      .then((r) => setCommands(r.commands ?? []))
      .catch(() => {})
  }, [])

  // 聊天侧 provider 上下文 = 活动会话所在机器的连接（ModelPicker/顶部模型跟随当前会话机器）
  const chatGw = useCallback(() => connsRef.current[activeRef.current], [])

  // provider 列表响应统一回写；顶部模型显示随活动机器的 active 模型修正
  const applyProviderResp = useCallback(
    (r: { profiles?: ProviderProfile[]; active?: ActiveModel; classifier?: ModelPointer }) => {
      setProviders(r.profiles ?? [])
      setActiveModel(r.active ?? { provider: '', model: '' })
      setModel(r.active?.model ?? '')
      setClassifier(r.classifier ?? {})
    },
    [],
  )

  const loadProviders = useCallback(() => {
    chatGw()?.listProviders().then(applyProviderResp).catch(() => {})
  }, [chatGw, applyProviderResp])

  // 切会话即重载该机器的 providers（修了「切到远程会话仍显示本地模型」的 bug）
  useEffect(() => {
    if (active) loadProviders()
  }, [active, loadProviders])

  const switchEffort = (level: string) => {
    chatGw()
      ?.setEffort(activeModel.provider, activeModel.model, level)
      .catch((e) => console.error('set_effort 失败:', e))
      .finally(() => loadProviders())
  }

  // 切模型：在当前会话连接上切（该机器该 bridge 下一轮生效），更新顶部显示
  const switchModel = (provider: string, model: string) => {
    chatGw()
      ?.setProvider(provider, model)
      .then((r) => {
        setActiveModel(r.active)
        if (r.model) setModel(r.model)
      })
      .catch(() => {})
  }

  // 设置面板改了某机器的 provider 后回调：若改的正是当前会话机器，刷新聊天侧
  const onProvidersChanged = useCallback(
    (machine: string) => {
      if (machine === (keyBackend(activeRef.current) || 'local')) loadProviders()
    },
    [loadProviders],
  )

  // 激活一个会话：无现成连接时先建立（target=null 为新会话），并同步连接指示灯。
  // connect→setActive→setConn 的握手只写在这一处，五个入口共用。
  const activate = useCallback(
    async (target: string | null, workspace = '', backend = 'local') => {
      // key = 会话前端身份（backend+thread）；target 为裸 thread（新会话时 null）
      let key = target ? sessionKey(backend, target) : ''
      // 乐观切换：建连可能长时间悬置（openConnection 只在 ready 时 resolve——
      // sidecar 重启/离线期间既不成功也不失败），先把 UI 落到目标会话（空内容 +
      // 黄灯示意），避免 view 已切、active 未动的脱节（如 cron 视图残留渲染成普通聊天）。
      // 晚到的建连结果经 seq 判废弃：用户已切走时不回拽视图。
      const seq = ++activationSeqRef.current
      if (key) {
        setActive(key)
        setPreview(null)
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
      if (workspace) setWorkspaceDir(workspace)
      setActive(key) // activeBackend 从 active 派生，无需单独设
      // 按连接真实状态点灯：openConnection 失败路径也会 resolve（乐观切换契约），
      // 硬编码 'open' 会在连接实际已断时点亮假绿灯
      setConn(connsRef.current[key]?.state ?? 'open')
      setPreview(null) // 切会话关掉预览，避免上个会话的文件残留
      return key
    },
    [openConnection, reloadHistory],
  )

  const openProjects = useCallback(() => {
    setNeedProjectHint(false) // 用户主动点「项目」标签，不是被新建会话逼过来的，不提示
    setView('projects')
    void refreshProjects(projectsMachine)
  }, [refreshProjects, projectsMachine])

  // 项目视图切机器（先选机器）
  const selectProjectsMachine = useCallback(
    (machine: string) => {
      setProjectsMachine(machine)
      void refreshProjects(machine)
    },
    [refreshProjects],
  )

  // 聊天必须绑定项目：无默认项目时阻断式跳去项目选择器，不放行空 workspace 会话。
  // 不做"有活跃项目就复用"的图方便捷径——workspaceDir 只是镜像当前会话绑的目录，
  // 可能是旧版本 cwd 兜底遗留、从未登记进项目列表，拿它当"合法当前项目"复用等于
  // 复活刚堵掉的口子。真正安全的复用信号是后端书签列表里显式登记的 default 项目。
  const requireProject = useCallback(
    (backend?: string, opts?: { skipRefresh?: boolean }) => {
      if (backend && backend !== projectsMachine) {
        // skipRefresh：调用方（goNewChat）已经手头有本 backend 的最新列表，
        // 不用 selectProjectsMachine 再重新拉一遍 listProjects
        if (opts?.skipRefresh) setProjectsMachine(backend)
        else selectProjectsMachine(backend)
      }
      setNeedProjectHint(true)
      setView('projects')
    },
    [projectsMachine, selectProjectsMachine],
  )

  // 在指定机器开新会话（方案甲：边栏每台机器各有「＋新对话」）。workspace 须非空——
  // 调用方（openProject 等）已保证，不再兜底放行空 workspace。
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
  const goNewChat = useCallback(
    async (backend: string) => {
      const workspace = await fetchDefaultProject(backend)
      if (workspace) {
        await newSession(backend, workspace)
      } else if (workspace === null) {
        // 拉取失败（连接波动）：走 requireProject 的 selectProjectsMachine 重新拉取重试
        requireProject(backend)
      } else {
        // 已拿到本机器最新列表（fetchDefaultProject 内已同步 projects state）、确认
        // 没有默认项目：跳选择器但不用再重新拉一遍刚拿到手的结果
        requireProject(backend, { skipRefresh: true })
      }
    },
    [fetchDefaultProject, newSession, requireProject],
  )
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

  const openScheduled = useCallback(() => setView('scheduled'), [])

  // 打开项目 = 在该机器开一条绑定到此项目的新会话（项目经 open 握手随会话绑定，
  // 不再先在共享连接上 setWorkspace 改进程态——那对新会话的独立连接无效）
  const openProject = useCallback(
    async (path: string, backend = 'local') => {
      try {
        setProjectsCurrent(path)
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
    setProjectsCurrent(path) // 与旧「点卡片即当前项目」的高亮语义保持一致
    setView('project')
    // 输入栏在项目页与聊天页共用同一份 input/attachments：进项目页先清空，
    // 免得上个会话的草稿/附件串到「在此项目开新会话」里
    setInput('')
    setAttachments([])
  }, [])

  // 项目主页输入岛：复用主输入栏（input/attachments/斜杠命令全共用），发送即在此项目
  // 新建会话并携带首条消息。新会话此刻还没进 active，override 自带目标与附件，规避渲染时序依赖。
  const startProjectChat = useCallback(async () => {
    if (!projectHome) return
    const text = input.trim()
    const atts = attachments
    if (!text && atts.length === 0) return
    // 机器离线时建连接会一直悬挂（既不成功也不失败）：先拦下并提示，别让下面清空后把
    // 用户输入吞掉。
    if (controlConns.current[projectHome.backend]?.state !== 'open') {
      toast.error(t('projhome.offline'))
      return
    }
    const key = await newSession(projectHome.backend, projectHome.path)
    sendRef.current({ text, key, workspace: projectHome.path, atts })
    // 清空放在发送派发之后：建会话若中途失败/悬挂，输入仍留在框里可重试，不丢字。
    setInput('')
    setAttachments([])
  }, [projectHome, newSession, input, attachments, t])

  // 项目主页专用 API：只认目标机器的控制连接，缺位返回 undefined——文件写操作
  // 绝不回退到别的机器（gwForBackend 的 anyGw 兜底对注册表类操作无害，对写文件有害）。
  // useCallback 稳定引用：ProjectHomePage 的加载 effect 依赖它，不稳会随 App 重渲染风暴重发
  const projectHomeApi = useCallback(
    () => (projectHome ? controlConns.current[projectHome.backend] : undefined),
    [projectHome],
  )

  // 项目主页 props 全部稳定引用：App 是流式事件的重渲染热点（后台会话每个 token 都
  // 触发 setStore），配合 ProjectHomePage 的 memo()，停在项目页时不再整页 reconcile
  // 当前项目的登记条目（name/default 都从这一次 find 取）
  const homeProject = projectHome
    ? projects.find((p) => p.path === projectHome.path)
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
    (id: string, enabled: boolean) =>
      void gwForBackend(projectHome?.backend ?? 'local')
        ?.toggleCronJob(id, enabled)
        .then(() => refreshCronJobs())
        .catch(() => {}),
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

  // 临时目录增减都发到当前会话的连接上（连接级/会话级状态），结果回写 folderStore
  const applyFolderOp = useCallback(
    async (op: (gw: Gateway) => Promise<{ folders: string[] }>) => {
      const tid = activeRef.current
      const gw = connsRef.current[tid]
      if (!gw) return
      try {
        const r = await op(gw)
        setFolderStore((s) => ({ ...s, [tid]: r.folders }))
      } catch {
        /* 忽略：连接波动时静默 */
      }
    },
    [],
  )

  // 打开目录浏览器（浏览的是当前会话所在机器的文件系统，而非本地原生选择器）
  const addFolder = useCallback(() => {
    if (connsRef.current[activeRef.current]) setAddingFolder(true)
  }, [])

  const removeFolder = useCallback(
    (path: string) => void applyFolderOp((gw) => gw.removeFolder(path)),
    [applyFolderOp],
  )

  // 拉取任务列表：唯一数据源，侧栏分组与管理页共用（CRUD 后经 onRefresh 刷新）
  useEffect(() => {
    if (conn === 'open') refreshCronJobs()
  }, [conn, cronVersion, refreshCronJobs])

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
      // 等后端删除提交后再清理本地 / 切会话——否则 activate(null) 触发的刷新
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
    setFolderStore((s) => {
      if (!(key in s)) return s
      const n = { ...s }
      delete n[key]
      return n
    })
    // 删除的是当前会话：另开一个新会话顶上
    if (key === activeRef.current) await activate(null)
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
            setAttachments((a) => [...a, { id: nid(), kind: 'image', dataUrl: url, name: f.name }])
          }
        }
        reader.readAsDataURL(f)
        continue
      }
      const path = window.lumi.getPathForFile?.(f) || ''
      if (path) setAttachments((a) => [...a, { id: nid(), kind: 'file', path, name: f.name }])
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

  // send/runCommand 的统一失败兜底：复位 running 之外必须把后端拒绝原因亮出来——
  // 之前只 resetRunning 会让「未绑定项目」这类拒绝表现成消息发了没反应，无声消失
  const reportSendFailure = (sid: string, err: unknown) => {
    resetRunning(sid)
    const message = err && typeof err === 'object' && 'message' in err ? String(err.message) : ''
    if (message) toast.error(message)
  }

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
    const busy = storeRef.current[sid]?.running ?? false
    if ((!text && attCount === 0) || busy || !gw) return
    // 主动发送即视为「回到对话」：强制贴底，确保自己的消息与随后的回复都在视野内
    setPinned(true)
    const files: AttachedFile[] = fileRefs.map((a) => ({ path: a.path, name: a.name }))
    setStore((s) => ({
      ...s,
      [sid]: {
        ...s[sid],
        items: [
          ...s[sid].items,
          {
            id: nid(),
            kind: 'user',
            text, // 可见正文只留用户输入；附件路径走 system-reminder，不污染气泡
            images: imgs.length ? imgs.map((a) => a.dataUrl) : undefined,
            files: files.length ? files : undefined,
            ts: Date.now(), // 与服务端落库的到达时刻近似一致，重载前后时间头不跳变
          },
        ],
        running: true,
      },
    }))
    if (!o) {
      setInput('')
      setAttachments([])
    }
    // 纯文本的已知斜杠命令走 run_command；带附件则一律走 send_message。
    // 注意此分支在乐观插入之前：/compact、/dream 这类不产生 checkpoint 的命令
    // 不该插条目（后端永远列不出，回合结束刷新时会当着用户的面消失）。
    if (attCount === 0 && text.startsWith('/')) {
      const [name, extra] = parseCommand(text)
      if (commands.some((c) => c.name === name)) {
        gw.runCommand(name, extra, toolMode).catch((err) => reportSendFailure(sid, err))
        return
      }
    }
    // 新会话首条消息：checkpoint 未落盘前后端列不出来，先乐观插入侧栏条目——
    // 否则首轮期间会话在侧栏缺席、切出去就回不来。回合结束的整表刷新会以
    // 后端真实数据替换（display_time 沿用后端的相对时间文案格式）。
    const tid = keyThread(sid)
    const be = keyBackend(sid)
    if (!sessionsRef.current.some((s) => s.thread_id === tid && beOf(s) === be)) {
      setSessions((prev) => [
        {
          thread_id: tid,
          first_message: text,
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
    // 文件附件只发路径数组：后端统一拼 <attached-file> 标签块给模型 + 写显示声明 items
    const filePaths = files.map((f) => f.path)
    let payload: string | unknown[] = text
    if (imgs.length > 0) {
      const blocks: unknown[] = text ? [{ type: 'text', text }] : []
      // 图片拆为 Anthropic 原生图片块（后端按模型再转 OpenAI/Bedrock 格式）
      for (const a of imgs) {
        const m = /^data:([^;]+);base64,(.*)$/s.exec(a.dataUrl)
        if (m) blocks.push({ type: 'image', source: { type: 'base64', media_type: m[1], data: m[2] } })
      }
      payload = blocks
    }
    gw.sendMessage(payload, toolMode, filePaths).catch((err) => reportSendFailure(sid, err))
  }
  sendRef.current = send // 供项目主页输入岛在 newSession 后调用（override 自带目标，无时序依赖）

  // 中止当前流式轮：后端取消 task 并补发 turn.complete，running 随之复位
  const stop = () => {
    connsRef.current[active]?.stop().catch(() => {})
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

  const decide = (decision: 'approve' | 'reject') =>
    resumeWith(
      decision === 'approve'
        ? { decision: 'approve' }
        : { decision: 'reject', message: t('approval.rejectedMessage') },
      'approval',
    )

  const streaming = items.some((it) => it.kind === 'assistant' && it.streaming)
  const hasMessages = items.length > 0
  // 连续工具分段只随 items 变化重算，避免每次渲染都扫描
  const segments = useMemo(() => groupItems(items), [items])
  // 复制按钮挂在每轮「最后一个 segment」之后——即整段助手输出的底部（像 Claude 的动作栏
  // 在消息末尾），而不是夹在文字与其后工具（如 ask）之间。规则：
  // - 一轮文字↔工具交错时，中间文字段是过程，不给复制；只复制本轮**最终**那段助手文字。
  // - 按钮落点 = 本轮最后一个可锚定 segment（文字本身，或其后的工具如 ask）下方；
  //   错误气泡（notice）不占锚点，避免复制按钮挂到红色错误下面。
  // - copyMap: segment key → 该轮最终助手文本；activeKey: 末轮（在飞轮）的锚点 key。
  //   渲染时只对 activeKey 这一条按 running 把关（历史轮始终可复制）；末轮 running=true
  //   （生成中 / ask 等中断 pending）不出，收尾（完成或 stop→turn.complete）才出。
  const { copyMap, activeKey } = useMemo(() => {
    const map = new Map<string, string>()
    let text: string | null = null // 本轮最终助手文字
    let lastKey: string | null = null // 本轮最后一个可锚定 segment 的 key
    for (const seg of segments) {
      const kind = seg.kind === 'item' ? seg.item.kind : 'tools'
      if (kind === 'user') {
        if (text && lastKey) map.set(lastKey, text) // 收尾上一轮
        text = null
        lastKey = null
      } else if (kind !== 'notice') {
        // 助手文字/工具才能锚定复制按钮；错误气泡(notice)跳过，不占锚点
        lastKey = segKey(seg)
        if (seg.kind === 'item' && seg.item.kind === 'assistant' && seg.item.text) {
          text = seg.item.text
        }
      }
    }
    let activeKey: string | null = null
    if (text && lastKey) {
      map.set(lastKey, text) // 末轮收尾，记锚点供 running 把关
      activeKey = lastKey
    }
    return { copyMap: map, activeKey }
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
  const submitCurrent = () => (view === 'project' ? void startProjectChat() : send())

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
  const feishuInfo = (channels[activeBackend] ?? []).find((c) => c.name === activeChannel)
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
  // 分母取会话真实模型（cur.ctxWindow）而非 desktop activeModel。无数据时环自隐藏、文字仍居中。
  const readonlyBar = (
    <div className="flex items-center gap-2 rounded-3xl border border-dashed border-line bg-surface/50 py-2 pl-4 pr-2.5 text-xs text-muted-foreground">
      <span className="flex-1 text-center">{t('chan.readonly')}</span>
      <ContextMeter usage={cur?.ctx} window={cur?.ctxWindow ?? 0} model={cur?.ctxModel ?? ''} />
    </div>
  )

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
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 px-3.5 pt-3">
          {attachments.map((a) =>
            a.kind === 'image' ? (
              <div key={a.id} className="relative group/att">
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
                className="relative group/att flex items-center gap-2 max-w-56 h-9 pl-2 pr-2.5 rounded-xl border border-line/40 bg-canvas"
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
        disabled={conn !== 'open' || observingCronRun}
        placeholder={placeholder}
        highlightLen={cmdToken.length}
        inputRef={inputRef}
      />
      <div className="flex items-center justify-between gap-3 px-3 pb-2.5">
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => fileInputRef.current?.click()}
            aria-label={t('composer.attach')}
            className="text-muted-foreground"
          >
            <Plus />
          </Button>
          {/* 文件夹是会话级授权：项目主页发送前尚无会话可挂（active 是上个会话，
              显示/改它都是错的），发送后可在聊天页添加。故项目模式下隐藏。 */}
          {!project && (
            <FolderMenu
              folders={folderStore[active] ?? []}
              onAdd={() => void addFolder()}
              onRemove={(p) => void removeFolder(p)}
            />
          )}
          <ModelPicker
            model={model}
            providers={providers}
            active={activeModel}
            machine={
              machines.filter((m) => m.enabled !== false).length > 1
                ? { name: machineName(activeBackend, machines), color: machineColor(activeBackend, machines) }
                : undefined
            }
            onSwitch={switchModel}
            onSwitchEffort={switchEffort}
          />
          <ApprovalModePicker
            value={toolMode}
            onChange={(m) => {
              setToolMode(m)
              // 实时推后端：改运行中会话的共享 context，对当前轮后续工具立即生效。
              // 未连接时静默失败——下一条消息自带 tool_mode 会重设 context。
              // 项目模式无活动会话，不推 active（它是上个会话）；toolMode 发送时随新会话生效。
              if (!project) connsRef.current[active]?.setToolMode(m).catch(() => {})
            }}
            classifierLabel={classifier.provider ? classifier.model : undefined}
          />
        </div>
        <div className="flex items-center gap-1.5">
          {/* 项目模式无活动会话：不读 cur.ctx（免后台流式 token 触发项目页重渲染），整块不渲染 */}
          {!project && <ContextMeter usage={cur?.ctx} window={contextWindow} model={model} />}
          {!project && (running || observingCronRun) && !approval && !clarify ? (
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
              disabled={
                (!input.trim() && attachments.length === 0) ||
                (!project && (running || conn !== 'open'))
              }
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
    [
      input, attachments, conn, model, providers, activeModel, machines, activeBackend,
      toolMode, classifier, menuOpen, matched, cmdSel, cmdToken, homeProjectInfo?.name,
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
  const railBg =
    (view === 'chat' || (!!cronRunThread && keyThread(active) === cronRunThread)) &&
    activeBgTasks.length > 0
  const showRail = view === 'cronjob' ? !!activeCronJob : view === 'chat' && railBg
  // 脉冲点 = 可见模块里确有东西在跑：隐藏的 bg 模块不算，直播中的 cron 执行算
  const railDot = (railBg && hasRunningBg) || cronLiveRuns.length > 0

  return (
    <div className="h-full flex flex-col bg-canvas">
      {!isMacTitleBar && (
        <AppTitleBar onNewChat={startNewChat} onOpenSettings={openSettings} />
      )}
      <div className="relative min-h-0 flex-1 flex">
      {/* 悬浮侧栏占位：宽度动画（侧栏本体绝对定位于其中，收起时向左滑出） */}
      <div
        className="relative shrink-0 transition-[width] duration-300 ease-out"
        style={{ width: sidebarOpen ? sidebarW.width + FLOAT_GAP * 2 : 0 }}
      >
      <Sidebar
        width={sidebarW.width}
        open={sidebarOpen}
        onToggle={toggleSidebar}
        showTitleDrag={isMacTitleBar}
        sessions={sessions}
        loadedBackends={loadedBackends}
        machines={machines}
        machineConn={machineConn}
        channels={channels}
        recentLimit={recentLimit}
        currentKey={view === 'chat' ? active : ''}
        conn={conn}
        model={model}
        activity={activity}
        projectsActive={view === 'projects' || view === 'project'}
        scheduledActive={view === 'scheduled'}
        cronJobs={cronJobs}
        readRuns={readRuns}
        cronRunning={cronRunning}
        activeCronJob={view === 'cronjob' ? activeCronJob : null}
        onOpenCronJob={openCronJob}
        onSelect={selectSession}
        onNew={() => startNewChat()}
        onNewChat={(backend) => void goNewChat(backend)}
        onReconnectMachine={(backend) => void reconnectMachine(backend)}
        onOpenProjects={openProjects}
        onOpenScheduled={openScheduled}
        onOpenSettings={openSettings}
        onPin={pinSession}
        onRename={renameSession}
        onDelete={setPendingDelete}
      />
      </div>
      {/* floating：把手从占位容器边缘贴回悬浮面板的可见右缘 */}
      {sidebarOpen && <ResizeHandle {...sidebarW} edge="right" floating />}

      <main className="flex-1 flex flex-col min-w-0">
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
            {/* 中段纯拖拽条：独立矩形，与按钮区互不重叠，无需挖洞 */}
            <div className={`flex-1 ${isMacTitleBar ? 'app-drag' : ''}`} />
          </div>
        )}
        {view === 'projects' ? (
          <ProjectsPage
            projects={projects}
            current={projectsCurrent}
            machines={machines}
            machine={projectsMachine}
            needProjectHint={needProjectHint}
            onSelectMachine={selectProjectsMachine}
            // 点项目一律进项目主页——主页输入岛本身就能在此项目开聊，故无论是主动浏览
            // 还是被「新建会话」阻断跳来（needProjectHint），落点一致，不再偶发跳到欢迎页。
            onOpen={(p) => openProjectHome(p, projectsMachine)}
            onNew={() => setShowNewProject(true)}
            onRemove={(path) =>
              setPendingRemoveProject(projects.find((p) => p.path === path) ?? null)
            }
            onRename={(path, name) => renameProjectInList(path, name, projectsMachine)}
            onSetDefault={(path, isDefault) => setProjectDefault(path, isDefault, projectsMachine)}
          />
        ) : view === 'project' && homeProjectInfo ? (
          <ProjectHomePage
            project={homeProjectInfo}
            isDefault={!!homeProject?.default}
            api={projectHomeApi}
            sessions={homeSessions}
            cronJobs={homeCronJobs}
            composerSlot={projectComposer}
            onBack={openProjects}
            onOpenSession={openHomeSession}
            onOpenScheduled={openScheduled}
            onToggleCron={toggleHomeCron}
          />
        ) : view === 'scheduled' ? (
          <CronPage
            api={gwForBackend}
            machines={machines}
            jobs={cronJobs}
            runningJobs={cronRunning}
            version={cronVersion}
            onOpenRun={(tid, jid) => void openCronJob(jid, tid)}
            onRefresh={refreshCronJobs}
          />
        ) : (
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
                    <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-auto">
                    <div className="max-w-3xl mx-auto w-full px-6 py-8 space-y-5">
                      {segments.map((seg) => {
                        const key = segKey(seg)
                        const node =
                          seg.kind === 'tools' ? (
                            <ToolGroup key={key} tools={seg.tools} />
                          ) : seg.kind === 'agent' ? (
                            <AgentGroup key={key} items={seg.items} />
                          ) : seg.kind === 'files' ? (
                            <FileCards
                              key={key}
                              files={seg.files}
                              onOpen={setPreview}
                              activePath={preview?.path}
                            />
                          ) : (
                            <ItemView key={key} item={seg.item} />
                          )
                        // 只对在飞的末轮（activeKey）按 running 把关；历史轮始终可复制。
                        // 非复制段直接渲染裸 node（不套 wrapper），仅复制段才包一层挂按钮。
                        const copyText =
                          key === activeKey && running ? undefined : copyMap.get(key)
                        if (!copyText) return node
                        return (
                          <div key={key} className="group/copy">
                            {node}
                            <div className="mt-1 -ml-1 opacity-0 group-hover/copy:opacity-100 transition-opacity">
                              <CopyButton text={copyText} />
                            </div>
                          </div>
                        )
                      })}
                      {/* 状态指示器常驻：运行中显示阶段文案，中断（审批/澄清）时
                          保持显示等待态，完成后退化为无文字的静止光点 */}
                      <StatusIndicator
                        items={items}
                        running={running || observingCronRun}
                        waiting={!!(approval || clarify)}
                        streaming={streaming}
                        thinkingText={thinkingText}
                        compacting={compacting}
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
                      {approval && <ApprovalDialog data={approval} onDecide={decide} />}
                      {clarify && (
                        <ClarifyDialog
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
            {view === 'chat' && preview && (
              <>
                <ResizeHandle {...previewW} edge="left" />
                <div style={{ width: previewW.width }} className="shrink-0 h-full">
                  <PreviewPanel file={preview} onClose={() => setPreview(null)} />
                </div>
              </>
            )}
          </div>
        )}
      </main>
      {/* 右侧两栏与左侧栏同级（不放进 main）：否则会被 main 内的 topStrip 压低一截，
          顶边对不上左侧栏。代价是它们上方那段不再是窗口拖拽区——本就被面板占满。 */}
      {/* 统一右栏：chat / cronjob 共用一个实例（同一开合状态、同一宽度）。
          cron 会话置顶「执行记录」，后台任务等通用模块只声明一次、两视图同构往下叠。
          enter 仅聊天视图：首个后台任务出现时整栏动画入场，不猛挤聊天栏；
          切视图时组件保持挂载，不会重放。 */}
      {showRail && (
        <>
          {railOpen && <ResizeHandle {...railW} edge="left" />}
          <RightRail
            width={railW.width}
            open={railOpen}
            onToggle={toggleRail}
            dot={railDot}
            enter={view === 'chat'}
          >
            {view === 'cronjob' && activeCronJob && (
              // key=任务 id：换任务即重挂，节折叠态不跨任务残留（整栏开合在 App，不受影响）
              <RunsSection
                key={activeCronJob}
                api={runsRailApi}
                jobId={activeCronJob}
                open={railOpen}
                activeThread={cronRunThread}
                readRuns={readRuns}
                version={cronVersion}
                liveRuns={cronLiveRuns}
                onPick={pickRun}
              />
            )}
            {railBg && (
              <BgTasksSection
                tasks={activeBgTasks}
                open={railOpen}
                onStop={stopBgTask}
                onDismiss={dismissBgTask}
                onClearFinished={clearFinishedBgTasks}
              />
            )}
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
          recentLimit={recentLimit}
          setRecentLimit={changeRecentLimit}
          machines={machines}
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
          title={t('projects.chooseOn', {
            machine: machines.find((m) => m.id === projectsMachine)?.name ?? projectsMachine,
          })}
          onPick={(p) => void createProject(p, basename(p), projectsMachine)}
          onCancel={() => setShowNewProject(false)}
        />
      )}
      {addingFolder && (
        <DirBrowser
          gw={chatGw()}
          title={t('folder.chooseOn', {
            machine: machines.find((m) => m.id === activeBackend)?.name ?? activeBackend,
          })}
          onPick={(p) => {
            void applyFolderOp((gw) => gw.addFolder(p))
            setAddingFolder(false)
          }}
          onCancel={() => setAddingFolder(false)}
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
  )
}

// 会话底部常驻状态指示器（参考 Claude）：
// - 运行中：光点 + 当前阶段文案 + 本轮计时；思考阶段右侧箭头点开看流式思考
// - 中断（审批/澄清/计划）：保持显示「等待确认…」，计时继续
// - 完成：退化为无文字的静止光点，留在最后一条消息下
// 阶段优先级：等待确认 > 工具执行中 > 思考中 > 正文输出中 > 兜底「正在处理…」。
function StatusIndicator({
  items,
  running,
  waiting,
  streaming,
  thinkingText,
  compacting,
}: {
  items: Item[]
  running: boolean
  waiting: boolean
  streaming: boolean
  thinkingText: string
  compacting: boolean
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [sec, setSec] = useState(0)
  const boxRef = useRef<HTMLPreElement>(null)
  // 计时跟随 running：开始时归零起跑，结束即停（中断 waiting 期间继续走）
  useEffect(() => {
    if (!running) return
    setSec(0)
    const id = setInterval(() => setSec((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [running])
  useEffect(() => {
    if (open && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [thinkingText, open])

  if (!running) {
    // 完成态：无文字的静止光点
    return (
      <div className="mt-2">
        <span className="lumi-orb lumi-orb-idle" />
      </div>
    )
  }

  let runningTool: ToolItem | undefined
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    // agent 工具的运行态由其专属卡片（AgentGroup）展示，底栏不再重复「正在执行子任务…」
    if (it.kind === 'tool' && !it.done && it.name !== 'agent') {
      runningTool = it
      break
    }
  }
  const thinking = !waiting && !runningTool && !streaming && !!thinkingText
  const label = waiting
    ? t('status.waiting')
    : compacting
      ? t('status.compacting')
      : runningTool
        ? t(TOOL_META[runningTool.name]?.status ?? 'status.tool')
        : thinking
          ? t('common.thinking')
          : streaming
            ? t('status.writing')
            : t('status.working')

  return (
    <div className="mt-2">
      <div className="flex items-center gap-2.5 text-muted-foreground text-sm">
        <span className="lumi-orb" />
        <span>{label}</span>
        {sec > 0 && <span className="text-xs opacity-60">· {sec}s</span>}
        {thinking && (
          <button
            onClick={() => setOpen((o) => !o)}
            className="px-1.5 text-muted-foreground hover:text-ink transition-colors"
          >
            <ChevronRight
              size={13}
              className={`transition-transform ${open ? 'rotate-90' : ''}`}
            />
          </button>
        )}
      </div>
      {thinking && open && (
        <pre
          ref={boxRef}
          className="text-xs mt-1.5 ml-6 px-3 py-2 rounded-lg bg-surface/60 border border-line/60 overflow-auto max-h-28 whitespace-pre-wrap text-muted-foreground/90 leading-relaxed"
        >
          {thinkingText}
        </pre>
      )}
    </div>
  )
}

// memo：流式期间每个 delta 都重建 items 数组，但未变更项保持对象身份，
// memo 让历史消息（尤其 ReactMarkdown 解析）不随每个 token 重渲染。
const ItemView = memo(function ItemView({ item }: { item: Exclude<Item, { kind: 'tool' }> }) {
  if (item.kind === 'user') {
    return (
      <div className="flex flex-col items-end gap-1.5">
        {/* 消息头：发送者 · 发送时刻。渠道消息两者都有；desktop 消息只有 ts
            （stream_response 统一落库的到达时刻），只显示时间 */}
        {(item.sender || item.ts) && (
          <div className="pr-1.5 text-[10.5px] text-muted-foreground/75">
            {item.sender}
            {item.ts ? `${item.sender ? ' · ' : ''}${msgTime(item.ts)}` : ''}
          </div>
        )}
        {item.images && item.images.length > 0 && (
          <div className="flex flex-wrap gap-1.5 justify-end max-w-[80%]">
            {item.images.map((src, i) => (
              <img
                key={i}
                src={src}
                alt=""
                className="max-h-52 rounded-2xl border border-line/40 object-cover"
              />
            ))}
          </div>
        )}
        {item.files && item.files.length > 0 && (
          <div className="flex flex-wrap gap-1.5 justify-end max-w-[80%]">
            {item.files.map((f, i) => (
              <span
                key={i}
                title={f.path}
                className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs border-primary/30 bg-primary/10 text-ink"
              >
                <FileText size={12} className="shrink-0 text-primary" />
                <span className="max-w-52 truncate">{f.name}</span>
              </span>
            ))}
          </div>
        )}
        {item.text && (
          <div className="selectable bg-surface rounded-3xl rounded-br-lg px-4 py-2.5 max-w-[80%] whitespace-pre-wrap wrap-anywhere">
            {item.text}
          </div>
        )}
      </div>
    )
  }
  if (item.kind === 'assistant') {
    return (
      <div className="md md-serif">
        <Markdown>{item.text}</Markdown>
      </div>
    )
  }
  return (
    <div className="selectable text-sm text-error/80 bg-error/5 rounded-xl px-3.5 py-2.5">
      {item.text}
    </div>
  )
})

// AI 消息下的复制按钮：悬停出现，点击复制 markdown 原文，1.5s 内显示「已复制」反馈。
function CopyButton({ text }: { text: string }) {
  const { t } = useI18n()
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      })
      .catch(() => {})
  }
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={copy}
      title={copied ? t('common.copied') : t('common.copy')}
      aria-label={t('common.copy')}
      className="text-muted-foreground"
    >
      {copied ? <Check className="text-success" /> : <Copy />}
    </Button>
  )
}

// 工具（单个或多个）统一渲染为一行自然语言摘要（参考 Claude：
// "Edited 2 files, ran a command, read a file ›"）。无卡片、低调融入文本流，
// 点击展开看每个工具的细节。运行中强制展开看进度，完成后默认折叠。
// groupItems 每次产出新的数组包装，但元素身份稳定：逐元素同身份即视为未变。
// ToolGroup / AgentGroup 的 memo 比较器共用，避免流式文本期间整组（含 diff 计算）重渲染。
const sameItems = (a: ToolItem[], b: ToolItem[]) =>
  a.length === b.length && a.every((x, i) => x === b[i])

const ToolGroup = memo(function ToolGroup({ tools }: { tools: ToolItem[] }) {
  const running = tools.some((t) => !t.done)
  const hasError = tools.some((t) => t.error)
  // override=null 时按 hasError 决定默认展开；出错的工具组默认展开但仍可手动收起
  const [override, setOverride] = useState<boolean | null>(null)
  const open = running || (override ?? hasError)
  const summary = running
    ? `${summarizeTools(tools.filter((t) => t.done)) || 'Working'}…`
    : summarizeTools(tools)

  return (
    <div>
      <button
        onClick={() => setOverride((o) => !(o ?? hasError))}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-ink transition"
      >
        {running && <span className="text-primary animate-pulse text-[10px]">●</span>}
        {!running && hasError && <span className="text-error text-[10px]">●</span>}
        <span className={hasError ? 'text-error' : ''}>{summary}</span>
        <ChevronRight
          size={14}
          className={`shrink-0 opacity-60 transition-transform ${open ? 'rotate-90' : ''}`}
        />
      </button>
      {open && (
        <div className="mt-1.5 ml-0.5 border-l border-line/40 pl-3 space-y-0.5">
          {tools.map((t) => (
            <ToolRow key={t.id} item={t} />
          ))}
        </div>
      )}
    </div>
  )
},
(prev, next) => sameItems(prev.tools, next.tools))

// 运行中卡片里最多同时显示的子工具行（旧的滚出，避免无限堆积撑开主流）
const SUBAGENT_WINDOW = 3

// 子代理段渲染：单个 → 滚动窗口卡片（SingleAgent）；并发多个 → 合并面板（AgentFleet）。
const AgentGroup = memo(
  function AgentGroup({ items }: { items: ToolItem[] }) {
    return items.length === 1 ? <SingleAgent item={items[0]} /> : <AgentFleet items={items} />
  },
  (prev, next) => sameItems(prev.items, next.items),
)

// 子工具数 + token 摘要。无子工具且无 token 时返回空串——历史恢复的卡片（子代理内部
// 活动不进 checkpoint）与刚启动尚未调工具的瞬间，都不显示误导性的「0 工具」。
const agentStats = (children: number, tokens: number, t: ReturnType<typeof useI18n>['t']) =>
  children || tokens
    ? `${children} ${t('subagent.tool')}${tokens ? ` · ${fmtTokens(tokens)}` : ''}`
    : ''

// 子代理 args.name（子代理类型名，如 explorer），缺失回退到序号
const agentName = (args: unknown, i: number): string =>
  argStr(asRecord(args).name) || `agent ${i + 1}`

// 子代理完成态的纯单行（不可展开）：静止光点 + 标签 + 详情 + 统计。单个与并发共用。
function DoneCard({ label, detail, stats }: { label: string; detail: string; stats: string }) {
  return (
    <div className="rounded-xl border border-line bg-panel flex items-center gap-2.5 px-3 py-2">
      <span className="lumi-orb lumi-orb-idle" />
      <span className="font-medium shrink-0">{label}</span>
      <span className="text-muted-foreground truncate flex-1">{detail}</span>
      {stats && <span className="text-muted-foreground text-xs tabular-nums shrink-0">{stats}</span>}
    </div>
  )
}

// 单个子代理卡片：运行中显示头部统计 + 最近 N 个子工具的有限滚动窗口（新行推入、旧行挤出）；
// 完成后收成纯单行（不可展开）。
function SingleAgent({ item }: { item: ToolItem }) {
  const { t } = useI18n()
  const children = item.children ?? []
  const tokens = (item.inTok ?? 0) + (item.outTok ?? 0)
  const title = toolTitle('agent', item.args)
  const stats = agentStats(children.length, tokens, t)

  if (item.done) {
    return <DoneCard label={t('subagent.label')} detail={title} stats={stats} />
  }
  return (
    <div className="rounded-xl border border-line bg-panel overflow-hidden">
      <div className="flex items-center gap-2.5 px-3 py-2">
        <span className="lumi-orb" />
        <span className="font-medium flex-1 truncate">{title}</span>
        {stats && <span className="text-muted-foreground text-xs tabular-nums shrink-0">{stats}</span>}
      </div>
      {children.length > 0 && <RunningWindow children={children} />}
    </div>
  )
}

// 并发子代理面板：卡片头「运行 N 个子 Agent」+ 总统计；每个 agent 一行（光点 · 名称 ·
// 当前动作 · 工具数）。全部完成后收成纯单行。
function AgentFleet({ items }: { items: ToolItem[] }) {
  const { t } = useI18n()
  const allDone = items.every((it) => it.done)
  const totalTools = items.reduce((n, it) => n + (it.children?.length ?? 0), 0)
  const totalTok = items.reduce((n, it) => n + (it.inTok ?? 0) + (it.outTok ?? 0), 0)
  const stats = agentStats(totalTools, totalTok, t)

  if (allDone) {
    const names = items.map((it, i) => agentName(it.args, i)).join(', ')
    return <DoneCard label={t('subagent.agentsDone', { n: items.length })} detail={names} stats={stats} />
  }
  return (
    <div className="rounded-xl border border-line bg-panel overflow-hidden">
      <div className="flex items-center gap-2.5 px-3 py-2">
        <span className="lumi-orb" />
        <span className="font-medium flex-1">{t('subagent.running', { n: items.length })}</span>
        {stats && <span className="text-muted-foreground text-xs tabular-nums shrink-0">{stats}</span>}
      </div>
      <div className="border-t border-line/70">
        {items.map((it, i) => (
          <FleetRow key={it.id} item={it} name={agentName(it.args, i)} />
        ))}
      </div>
    </div>
  )
}

// 并发面板单行：光点 + agent 名 + 当前动作（最后一个子工具，运行中金色高亮）+ 工具数。
function FleetRow({ item, name }: { item: ToolItem; name: string }) {
  const { t } = useI18n()
  const children = item.children ?? []
  const last = children[children.length - 1]
  const running = !item.done
  const Icon = item.done ? Check : last ? toolIcon(last.name) : Bot
  const action = item.done
    ? t('subagent.done')
    : last
      ? toolTitle(last.name, last.args)
      : t('common.thinking')
  return (
    <div className="flex items-center gap-2.5 px-3 py-1.5 border-t border-line/40 first:border-t-0">
      <span className={`subagent-dot ${running ? 'subagent-dot-run' : 'subagent-dot-done'}`} />
      <span className="font-medium shrink-0 w-20 truncate">{name}</span>
      <span className="flex items-center gap-1.5 text-muted-foreground text-xs flex-1 min-w-0">
        <Icon size={13} className={`shrink-0 ${running ? 'text-primary' : 'text-success/80'}`} />
        <span className="truncate">{action}</span>
      </span>
      <span className="text-muted-foreground text-[11px] tabular-nums shrink-0">
        {children.length} {t('subagent.tool')}
      </span>
    </div>
  )
}

// 运行中的有限工具窗口：只保留最近 SUBAGENT_WINDOW 行。新子工具从底部推入（subtool-enter），
// 超出窗口的最旧行标记 leaving 向上淡出收起（subtool-leave），动画结束后真正移除。
// seen 记录已入场过的 toolCallId，避免 leaving 行移除后又被重新加回。
function RunningWindow({ children }: { children: SubTool[] }) {
  const [rows, setRows] = useState<{ c: SubTool; leaving: boolean }[]>([])
  const seen = useRef<Set<string>>(new Set())

  useEffect(() => {
    setRows((rows) => {
      // 同步已显示行的最新状态（done/error）
      let next = rows.map((r) => {
        const fresh = children.find((c) => c.toolCallId === r.c.toolCallId)
        return fresh ? { ...r, c: fresh } : r
      })
      // 追加首次出现的子工具
      const added = children.filter((c) => !seen.current.has(c.toolCallId))
      added.forEach((c) => seen.current.add(c.toolCallId))
      next = [...next, ...added.map((c) => ({ c, leaving: false }))]
      // 活跃行超出窗口 → 最旧的几条标记离场
      const active = next.filter((r) => !r.leaving)
      const overflow = active.length - SUBAGENT_WINDOW
      if (overflow > 0) {
        const leave = new Set(active.slice(0, overflow).map((r) => r.c.toolCallId))
        next = next.map((r) => (leave.has(r.c.toolCallId) ? { ...r, leaving: true } : r))
      }
      return next
    })
  }, [children])

  const drop = (id: string) => setRows((rows) => rows.filter((r) => r.c.toolCallId !== id))

  // animationend 兜底：窗口后台化等场景下浏览器可能不派发离场动画结束事件，
  // 每个 leaving 行额外排一个一次性定时器移除，避免隐形僵尸行永久残留。drop 幂等。
  const scheduled = useRef<Set<string>>(new Set())
  useEffect(() => {
    for (const r of rows) {
      if (r.leaving && !scheduled.current.has(r.c.toolCallId)) {
        scheduled.current.add(r.c.toolCallId)
        window.setTimeout(() => drop(r.c.toolCallId), 320)
      }
    }
  }, [rows])

  return (
    <div className="border-t border-line/70 pl-7 pr-2.5 py-1">
      {rows.map(({ c, leaving }) => (
        <div
          key={c.toolCallId}
          className={leaving ? 'subtool-leave' : 'subtool-enter'}
          onAnimationEnd={leaving ? () => drop(c.toolCallId) : undefined}
        >
          <SubToolRow child={c} />
        </div>
      ))}
    </div>
  )
}

// 子工具行：图标 + 人类可读标题。运行中（!done）金色高亮，完成绿勾，出错红色。
function SubToolRow({ child }: { child: SubTool }) {
  const running = !child.done
  const Icon = child.done && !child.error ? Check : toolIcon(child.name)
  return (
    <div className="flex items-center gap-2.5 px-1.5 py-1 text-sm">
      <Icon
        size={15}
        className={`shrink-0 ${running ? 'text-primary' : child.error ? 'text-error' : 'text-success/80'}`}
      />
      <span className={`truncate ${running ? 'text-ink' : child.error ? 'text-error' : 'text-muted-foreground'}`}>
        {toolTitle(child.name, child.args)}
      </span>
    </div>
  )
}

// 展开后的工具明细行：图标 + 人类可读标题 + 旋转箭头，点击看输出/diff。
// 出错的工具行红色高亮并默认展开；edit/write 渲染 +/- diff 而非裸输出。
const ToolRow = memo(function ToolRow({ item }: { item: ToolItem }) {
  const { t } = useI18n()
  const errored = !!item.error
  // edit/write 展示 diff；出错时优先展示错误输出而非 diff
  const diff = errored ? null : toolDiff(item.name, item.args)
  const hasOutput = item.done && !!item.output
  const hasDetail = !!diff || hasOutput
  const [override, setOverride] = useState<boolean | null>(null)
  const open = override ?? errored
  const Icon = toolIcon(item.name)
  return (
    <div className="rounded-lg overflow-hidden">
      <button
        onClick={() => hasDetail && setOverride((o) => !(o ?? errored))}
        className={`w-full px-2 py-1.5 flex items-center gap-2.5 text-left text-sm rounded-lg ${hasDetail ? 'hover:bg-white/5' : 'cursor-default'}`}
      >
        <Icon
          size={15}
          className={`shrink-0 ${!item.done ? 'text-primary animate-pulse' : errored ? 'text-error' : 'text-muted-foreground'}`}
        />
        <span className={`truncate flex-1 ${errored ? 'text-error' : 'text-ink/80'}`}>
          {toolTitle(item.name, item.args)}
        </span>
        {hasDetail && (
          <ChevronRight
            size={13}
            className={`shrink-0 text-muted-foreground transition-transform ${open ? 'rotate-90' : ''}`}
          />
        )}
      </button>
      {open && diff && <DiffView lines={diff} />}
      {open && !diff && hasOutput && (
        <pre
          className={`text-xs ml-[26px] mr-1 mb-1 px-3 py-2 rounded-lg bg-canvas/60 overflow-auto max-h-60 whitespace-pre-wrap ${errored ? 'text-error/90' : 'text-muted-foreground/90'}`}
        >
          {item.output.slice(0, 4000)}
          {item.output.length > 4000 && '\n' + t('common.truncated')}
        </pre>
      )}
    </div>
  )
})

// edit/write 的行级 diff 视图：新增行绿底、删除行红底、上下文行淡显。
function DiffView({ lines }: { lines: DiffLine[] }) {
  return (
    <pre className="text-xs ml-[26px] mr-1 mb-1 px-2 py-2 rounded-lg bg-canvas/60 overflow-auto max-h-72 leading-relaxed">
      {lines.map((l, i) => (
        <div
          key={i}
          className={l.kind === 'add' ? 'bg-success/10' : l.kind === 'del' ? 'bg-error/10' : ''}
        >
          <span
            className={`select-none ${l.kind === 'add' ? 'text-success' : l.kind === 'del' ? 'text-error' : 'text-muted-foreground/40'}`}
          >
            {l.kind === 'add' ? '+ ' : l.kind === 'del' ? '- ' : '  '}
          </span>
          <span className={l.kind === 'ctx' ? 'text-muted-foreground/70' : 'text-ink/90'}>{l.text || ' '}</span>
        </div>
      ))}
    </pre>
  )
}

// 文本提取小工具（toolTitle 标题提取共用；clip/basename 在 lib/utils）
const argStr = (v: unknown) => (typeof v === 'string' ? v : '')
// 把未知的工具 args 安全收成 Record，便于按字段取值（toolTitle / agentName 共用）
const asRecord = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' ? (v as Record<string, unknown>) : {}

// 每个工具的展示元数据（图标 + 动作动词/名词 + 人类可读标题提取）集中在一张表，
// 新增工具只需加一行。icon 驱动 ToolRow 图标，verb/noun 驱动 summarizeTools 聚合，
// title 从 args 提取非技术用户看得懂的标题。
type ToolMeta = {
  icon: LucideIcon
  verb: string
  noun: string
  status: string // 运行中的状态指示器文案 i18n key（动作级粒度）
  title: (a: Record<string, unknown>, name: string) => string
}
const fileTitle = (a: Record<string, unknown>, name: string) =>
  argStr(a.file_path) ? basename(argStr(a.file_path)) : name
const searchTitle = (a: Record<string, unknown>) =>
  argStr(a.pattern) ? `Search ${clip(argStr(a.pattern), 48)}` : 'Search'

const TOOL_META: Record<string, ToolMeta> = {
  bash: { icon: SquareTerminal, verb: 'Ran', noun: 'command', status: 'status.runCommand', title: (a) => clip(argStr(a.description) || argStr(a.command) || 'Run command') },
  read: { icon: FileText, verb: 'Read', noun: 'file', status: 'status.readFile', title: fileTitle },
  write: { icon: FilePlus, verb: 'Wrote', noun: 'file', status: 'status.editFile', title: fileTitle },
  edit: { icon: FilePen, verb: 'Edited', noun: 'file', status: 'status.editFile', title: fileTitle },
  grep: { icon: Search, verb: 'Searched', noun: '', status: 'status.searching', title: searchTitle },
  glob: { icon: Search, verb: 'Searched', noun: '', status: 'status.searching', title: searchTitle },
  agent: { icon: Bot, verb: 'Ran', noun: 'subagent', status: 'status.subtask', title: (a) => clip(argStr(a.prompt) || argStr(a.name) || 'Run subagent') },
  todo: { icon: ListChecks, verb: 'Updated', noun: 'todo', status: 'status.tool', title: () => 'Update todos' },
}

const toolIcon = (name: string): LucideIcon => TOOL_META[name]?.icon ?? Wrench

const toolAction = (name: string): { verb: string; noun: string } => {
  const m = TOOL_META[name]
  return m ? { verb: m.verb, noun: m.noun } : { verb: 'Used', noun: name }
}

// 聚合成 "Edited 2 files, ran a command, read a file" 式自然语言摘要：
// 同动作合并计数，首个短语首字母大写、其余句中小写。
function summarizeTools(tools: ToolItem[]): string {
  if (tools.length === 0) return ''
  const order: string[] = []
  const agg = new Map<string, { verb: string; noun: string; n: number }>()
  for (const t of tools) {
    const a = toolAction(t.name)
    const key = `${a.verb}|${a.noun}`
    if (!agg.has(key)) {
      agg.set(key, { ...a, n: 0 })
      order.push(key)
    }
    agg.get(key)!.n++
  }
  const phrases = order.map((k) => {
    const { verb, noun, n } = agg.get(k)!
    if (!noun) return n === 1 ? verb : `${verb} ${n} times`
    return n === 1 ? `${verb} a ${noun}` : `${verb} ${n} ${noun}s`
  })
  return phrases
    .map((p, i) => (i === 0 ? p : p.charAt(0).toLowerCase() + p.slice(1)))
    .join(', ')
}

// 从工具 args 提取人类可读标题（非技术用户看得懂），而非 dump raw JSON。
// 提取规则定义在 TOOL_META[name].title；未知工具回退到第一个字符串字段。
function toolTitle(name: string, args: unknown): string {
  const a = asRecord(args)
  const m = TOOL_META[name]
  if (m) return m.title(a, name)
  const first = Object.values(a).find((v) => typeof v === 'string')
  return first ? clip(String(first)) : name
}

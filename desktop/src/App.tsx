import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
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
  Copy,
  Check,
  Square,
  Plus,
  X,
  PanelRight,
  type LucideIcon,
} from 'lucide-react'
import { Gateway, type ConnState } from './gateway'
import type {
  ActiveModel,
  AttachedFile,
  BgTask,
  CronJob,
  HistoryItem,
  Item,
  Project,
  ProviderProfile,
  SessionMeta,
  SlashCommand,
  SubTool,
  WireEvent,
} from './types'
import { ApprovalDialog } from './components/ApprovalDialog'
import { ClarifyDialog, ASK_CANCELLED } from './components/ClarifyDialog'
import { PlanDialog, PLAN_REJECTED } from './components/PlanDialog'
import { Sidebar } from './components/Sidebar'
import { BgTasksDrawer } from './components/BgTasksDrawer'
import { CronPage, RunsRail } from './components/CronPage'
import { ConfirmDialog } from './components/ConfirmDialog'
import { SettingsDialog } from './components/SettingsDialog'
import { ModelPicker } from './components/ModelPicker'
import { ContextMeter, type CtxUsage } from './components/ContextMeter'
import { ProjectsPage } from './components/ProjectsPage'
import { NewProjectDialog } from './components/NewProjectDialog'
import { FolderMenu } from './components/FolderMenu'
import { CommandMenu } from './components/CommandMenu'
import { Composer } from './components/Composer'
import { toast } from './components/Toast'
import { isCommandMode, parseCommand, matchCommands } from './slash'
import { toolDiff, type DiffLine } from './diff'
import { clip, basename, fmtTokens } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useTheme } from './theme'
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
  seg.kind === 'tools' ? `g${seg.tools[0].id}` : seg.kind === 'agent' ? `a${seg.items[0].id}` : `i${seg.item.id}`

// load_history 的历史项 → 前端 Item
function restore(h: HistoryItem): Item {
  if (h.kind === 'user') return { id: nid(), kind: 'user', text: h.text ?? '', images: h.images, files: h.files }
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
function applyChildEvent(
  s: SessionState,
  parentRun: string,
  type: string,
  payload: any,
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
  approval: Record<string, unknown> | null
  clarify: Record<string, unknown> | null
  plan: Record<string, unknown> | null
  // 最近一次模型调用的上下文用量（用于输入栏的上下文进度环）；首轮前为 undefined
  ctx?: CtxUsage
}
const emptySession = (items: Item[] = []): SessionState => ({
  items,
  running: false,
  thinkingText: '',
  approval: null,
  clarify: null,
  plan: null,
})

// 从 LangChain usage_metadata 提炼上下文环所需快照。input_tokens 含缓存命中部分，
// 直接作为「当前上下文占用」；缺字段（如非流式补发不带 input_tokens）返回 undefined。
const ctxFromUsage = (u: any): CtxUsage | undefined => {
  if (!u || typeof u.input_tokens !== 'number') return undefined
  return {
    used: u.input_tokens,
    output: u.output_tokens ?? 0,
    cacheRead: u.input_token_details?.cache_read ?? 0,
  }
}

export default function App() {
  const [store, setStore] = useState<Record<string, SessionState>>({})
  const [active, setActive] = useState('')
  const [conn, setConn] = useState<ConnState>('connecting')
  const [model, setModel] = useState('')
  // 进程级工作目录 = 当前项目（gateway.ready 下发；切项目对整个 app 生效）
  const [workspaceDir, setWorkspaceDir] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [showNewProject, setShowNewProject] = useState(false)
  const [pendingRemoveProject, setPendingRemoveProject] = useState<Project | null>(null)
  // 各会话临时添加的额外可访问目录（连接级状态的前端镜像）
  const [folderStore, setFolderStore] = useState<Record<string, string[]>>({})
  const [input, setInput] = useState('')
  const [commands, setCommands] = useState<SlashCommand[]>([])
  const [cmdSel, setCmdSel] = useState(0)
  const [cmdDismissed, setCmdDismissed] = useState(false)
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [providers, setProviders] = useState<ProviderProfile[]>([])
  const [activeModel, setActiveModel] = useState<ActiveModel>({ provider: '', model: '' })
  const [showSettings, setShowSettings] = useState(false)
  const openSettings = useCallback(() => setShowSettings(true), [])
  const [pendingDelete, setPendingDelete] = useState<SessionMeta | null>(null)
  const [themePref, setThemePref] = useTheme()
  const { t } = useI18n()
  const [notify, setNotify] = useState(() => localStorage.getItem('lumi-notify') === '1')
  // 图片嵌入消息（dataUrl→image 块）；其它文件只带绝对路径，发送时写进消息文本，
  // 由 Agent 用工具读取（不在此预授权，交给现有权限流程）
  const [attachments, setAttachments] = useState<Attachment[]>([])
  // 主区视图：聊天 / 项目管理页 / 定时任务管理页 / 任务会话视图（某任务的某次执行对话 + Runs 侧栏）
  const [bgTasks, setBgTasks] = useState<BgTask[]>([]) // 后台任务全量快照（按 thread 过滤展示）
  const [bgDrawerOpen, setBgDrawerOpen] = useState(false) // 后台任务右栏开关（默认关，有任务时头部出现 PanelRight）
  const [view, setView] = useState<'chat' | 'projects' | 'scheduled' | 'cronjob'>('chat')
  const [cronRunning, setCronRunning] = useState<string[]>([]) // 运行中任务名
  const [cronVersion, setCronVersion] = useState(0) // 递增触发 cron 数据刷新
  const [cronJobs, setCronJobs] = useState<CronJob[]>([]) // 侧栏任务分组数据
  const [activeCronJob, setActiveCronJob] = useState<string | null>(null) // 任务会话视图当前任务
  const [cronRunThread, setCronRunThread] = useState<string | null>(null) // 当前选中的执行会话
  // 每任务未读结果计数（任务完成而你不在看它时 +1），持久化到 localStorage
  const [cronUnread, setCronUnread] = useState<Record<string, number>>(() => {
    try {
      return JSON.parse(localStorage.getItem('lumi-cron-unread') || '{}')
    } catch {
      return {}
    }
  })
  // 已查看过的执行会话（Runs 栏蓝点 = 未读，点开即消失），持久化
  const [readRuns, setReadRuns] = useState<Record<string, true>>(() => {
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
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // handleEvent 是 []-依赖的稳定回调，通过 ref 读取最新的 store / 通知开关 / 翻译
  const storeRef = useRef<Record<string, SessionState>>({})
  const notifyRef = useRef(notify)
  const tRef = useRef(t)
  // 临时目录是连接级（bridge 内存）状态：重连得到全新 bridge 后需重放，故镜像到 ref
  const folderStoreRef = useRef<Record<string, string[]>>({})

  useEffect(() => {
    folderStoreRef.current = folderStore
  }, [folderStore])
  useEffect(() => {
    activeRef.current = active
  }, [active])
  useEffect(() => {
    viewRef.current = view
  }, [view])
  useEffect(() => {
    activeCronJobRef.current = activeCronJob
  }, [activeCronJob])
  useEffect(() => {
    localStorage.setItem('lumi-cron-unread', JSON.stringify(cronUnread))
  }, [cronUnread])
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
  const approval = cur?.approval ?? null
  const clarify = cur?.clarify ?? null
  const plan = cur?.plan ?? null
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

  // 每个会话的活动态，喂给侧栏显示圆点：attention=等你处理（审批/澄清/计划），running=处理中。
  // store 每个流式 token 都换新身份，内容不变时复用上一个对象，避免 Sidebar 每 token 重渲染。
  const activityRef = useRef<Record<string, 'running' | 'attention'>>({})
  const activity = useMemo(() => {
    const m: Record<string, 'running' | 'attention'> = {}
    for (const tid in store) {
      const s = store[tid]
      if (s.approval || s.clarify || s.plan) m[tid] = 'attention'
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
  const handleEvent = useCallback((ev: WireEvent) => {
    const { type, payload } = ev
    // cron 事件是进程级广播（与会话无关），在 session 路由之前单独处理
    if (type === 'cron.running') {
      setCronRunning(payload.names ?? [])
      return
    }
    // 后台任务变更：全量快照广播（进程级），整列替换，前端按 thread 过滤展示
    if (type === 'bg_tasks.update') {
      setBgTasks(payload.tasks ?? [])
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
      setCronVersion((v) => v + 1)
      // 正在看该任务的会话视图时不算未读，其余情况该任务未读 +1
      const viewingThisJob =
        viewRef.current === 'cronjob' && activeCronJobRef.current === payload.job_id
      if (!viewingThisJob) {
        setCronUnread((u) => ({ ...u, [payload.job_id]: (u[payload.job_id] ?? 0) + 1 }))
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
    const sid = ev.session_id ?? ''
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
        const first = storeRef.current[sid]?.items.find((it) => it.kind === 'user')
        body = first && first.kind === 'user' ? first.text : ''
      } else if (type === 'approval.request') {
        title = t('approval.title')
        body = (payload.tool_calls ?? []).map((c: { name?: string }) => c.name).join(', ')
      } else if (type === 'clarify.request') {
        title = t('clarify.title')
        body = payload.questions?.[0]?.question ?? ''
      } else if (type === 'plan.request') {
        title = t('plan.title')
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
        case 'message.complete':
          n = { ...s, items: finishStreaming(s.items), ctx: ctxFromUsage(payload.usage) ?? s.ctx }
          break
        case 'tool.start': {
          const tcid = payload.tool_call_id ?? ''
          // 去重：ask 等 BYPASS 工具中断→resume 后会用同一 tool_call_id 重发 start
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
        case 'approval.request':
          n = { ...s, approval: payload }
          break
        case 'clarify.request':
          n = { ...s, clarify: payload }
          break
        case 'plan.request':
          n = { ...s, plan: payload }
          break
        case 'turn.complete':
          n = {
            ...s,
            running: false,
            items: finishStreaming(s.items),
            ctx: ctxFromUsage(payload.usage) ?? s.ctx,
          }
          break
        case 'error':
          // 出错中断的流（bridge 只发 error、无 message.complete）也要收尾气泡
          n = {
            ...s,
            running: false,
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
    (targetThread: string | null): Promise<string> => {
      return new Promise((resolve) => {
        void (async () => {
          const { wsUrl } = await window.lumi.getConnection()
          const gw = new Gateway(wsUrl)
          let myThread = ''
          let ready = false
          gw.onEvent((ev) => {
            if (ev.type === 'gateway.ready') {
              setModel((m) => m || ev.payload.model || '')
              if (ev.payload.workspace) setWorkspaceDir(ev.payload.workspace)
              if (ready) {
                // 重连：服务端给的是全新 bridge（新 session_id），切回本连接原 thread
                // 恢复后端绑定，否则会丢弃原会话、并多出一个幽灵空会话。
                // 新 bridge 的临时目录为空，需重放本会话已添加的目录，否则徽标显示
                // 有目录而后端实际访问不到。
                if (myThread) {
                  void gw.switchSession(myThread)
                  for (const f of folderStoreRef.current[myThread] ?? []) {
                    void gw.addFolder(f)
                  }
                }
                return
              }
              ready = true
              // 初始拉取后台任务快照（之后变更经 bg_tasks.update 推送）
              void gw
                .listBgTasks()
                .then((r) => setBgTasks(r.tasks))
                .catch(() => {})
              if (targetThread) {
                // 已有会话：切到该 thread 并加载历史
                void (async () => {
                  await gw.switchSession(targetThread)
                  const r = await gw.loadHistory(targetThread)
                  myThread = targetThread
                  connsRef.current[targetThread] = gw
                  setStore((s) => ({ ...s, [targetThread]: emptySession(r.items.map(restore)) }))
                  resolve(targetThread)
                })()
              } else {
                // 新会话：用握手分配的 thread
                myThread = ev.session_id ?? ''
                connsRef.current[myThread] = gw
                setStore((s) => ({ ...s, [myThread]: emptySession() }))
                resolve(myThread)
              }
            } else {
              handleEvent(ev)
            }
          })
          gw.onState((st) => {
            if (myThread && myThread === activeRef.current) setConn(st)
          })
          gw.connect()
        })()
      })
    },
    [handleEvent],
  )

  // 初始：开一条新会话连接
  useEffect(() => {
    let disposed = false
    void (async () => {
      const tid = await openConnection(null)
      if (!disposed) {
        setActive(tid)
        setConn('open')
      }
    })()
    return () => {
      disposed = true
      Object.values(connsRef.current).forEach((g) => g.close())
    }
  }, [openConnection])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [items, running, approval, clarify, plan])

  useEffect(() => {
    if (conn === 'open' && !running) inputRef.current?.focus()
  }, [conn, running, active])

  // 会话管理 / cron RPC 操作全局资源，与连接当前 thread 无关，任一活跃连接皆可。
  // 稳定引用（useCallback []）：作为 CronPage 的 api prop，避免每次渲染触发其刷新。
  const anyGw = useCallback(
    () => connsRef.current[activeRef.current] ?? Object.values(connsRef.current)[0],
    [],
  )

  const stopBgTask = useCallback(
    (taskId: string) => {
      // 乐观置为 failed，等下一条 bg_tasks.update 校正
      setBgTasks((ts) =>
        ts.map((x) => (x.task_id === taskId ? { ...x, status: 'failed' as const } : x)),
      )
      void anyGw()?.stopBgTask(taskId).catch(() => {})
    },
    [anyGw],
  )

  const dismissBgTask = useCallback(
    (taskId: string) => {
      setBgTasks((ts) => ts.filter((x) => x.task_id !== taskId)) // 乐观移除
      void anyGw()?.dismissBgTask(taskId).catch(() => {})
    },
    [anyGw],
  )

  const clearFinishedBgTasks = useCallback(() => {
    // 乐观清除本会话终态任务
    setBgTasks((ts) => ts.filter((x) => x.status === 'running' || x.thread_id !== activeRef.current))
    void anyGw()?.clearFinishedBgTasks().catch(() => {})
  }, [anyGw])

  // 当前会话的后台任务：一次 memo 派生，稳定引用避免 drawer 子树随每次 bg_tasks.update 重渲染
  const activeBgTasks = useMemo(
    () => bgTasks.filter((tk) => tk.thread_id === active),
    [bgTasks, active],
  )
  const hasRunningBg = activeBgTasks.some((tk) => tk.status === 'running')

  const refreshSessions = useCallback(async () => {
    try {
      const r = await anyGw()?.listSessions()
      if (r?.sessions) setSessions(r.sessions)
    } catch {
      /* 忽略：连接波动时静默 */
    }
  }, [anyGw])

  const refreshProjects = useCallback(async () => {
    try {
      const r = await anyGw()?.listProjects()
      if (r) {
        setProjects(r.projects)
        setWorkspaceDir(r.current)
      }
    } catch {
      /* 忽略：连接波动时静默 */
    }
  }, [anyGw])

  // 只在回合结束（running 落回 false）和切会话时刷新：发送时刷新没有新信息
  // （首条消息尚未落 checkpoint），白白多一次全量 checkpoint 扫描。
  useEffect(() => {
    if (active && !running) void refreshSessions()
  }, [active, running, refreshSessions])

  // 拉取斜杠命令（技能命令，按项目动态）。技能目录随项目变化，故进入命令模式时刷新。
  const loadCommands = useCallback(() => {
    anyGw()
      ?.listCommands()
      .then((r) => setCommands(r.commands ?? []))
      .catch(() => {})
  }, [anyGw])

  // provider 列表响应（list / save / delete 同形）统一回写
  const applyProviderResp = useCallback(
    (r: { profiles?: ProviderProfile[]; active?: ActiveModel }) => {
      setProviders(r.profiles ?? [])
      setActiveModel(r.active ?? { provider: '', model: '' })
    },
    [],
  )

  // 拉取模型供应商 profile 列表 + active
  const loadProviders = useCallback(() => {
    anyGw()?.listProviders().then(applyProviderResp).catch(() => {})
  }, [anyGw, applyProviderResp])

  useEffect(() => {
    if (active) loadProviders()
  }, [active, loadProviders])

  // 切换当前 active 模型的思考档位：持久化后刷新列表（thinking 数据随之更新）。
  // 失败（能力数据更新使档位失效等）也刷新，让 UI 回到后端真实状态而非静默不动。
  const switchEffort = (level: string) => {
    anyGw()
      ?.setEffort(activeModel.provider, activeModel.model, level)
      .catch((e) => console.error('set_effort 失败:', e))
      .finally(() => loadProviders())
  }

  // 切换模型：在当前会话的连接上切（该 bridge 下一轮生效），并更新顶部模型显示
  const switchModel = (provider: string, model: string) => {
    connsRef.current[active]
      ?.setProvider(provider, model)
      .then((r) => {
        setActiveModel(r.active)
        if (r.model) setModel(r.model)
      })
      .catch(() => {})
  }

  const saveProvider = (draft: Partial<ProviderProfile>) => {
    anyGw()?.saveProvider(draft).then(applyProviderResp).catch(() => {})
  }

  const deleteProvider = (id: string) => {
    anyGw()?.deleteProvider(id).then(applyProviderResp).catch(() => {})
  }

  const testProvider = (baseUrl: string, apiKey: string, model: string) =>
    anyGw()?.testProvider(baseUrl, apiKey, model) ??
    Promise.resolve({ ok: false, error: t('sidebar.disconnected') })

  // 激活一个会话：无现成连接时先建立（target=null 为新会话），并同步连接指示灯。
  // connect→setActive→setConn 的握手只写在这一处，五个入口共用。
  const activate = useCallback(
    async (target: string | null) => {
      let tid = target
      if (!tid || !connsRef.current[tid]) {
        // 建立期间以 connecting 示意（sidecar 不可用时指示灯保持黄色而非静默无反应）
        setConn('connecting')
        tid = await openConnection(target)
      }
      setActive(tid)
      setConn('open')
      return tid
    },
    [openConnection],
  )

  const newSession = useCallback(async () => {
    setView('chat')
    await activate(null)
    void refreshSessions()
  }, [activate, refreshSessions])

  const selectSession = useCallback(
    async (tid: string) => {
      setView('chat')
      if (tid !== activeRef.current) await activate(tid)
    },
    [activate],
  )

  const openScheduled = useCallback(() => setView('scheduled'), [])

  const openProjects = useCallback(() => {
    setView('projects')
    void refreshProjects()
  }, [refreshProjects])

  // 切换项目：set_workspace（进程级）→ 另开新会话回到聊天；点当前项目则直接回聊天
  const openProject = useCallback(
    async (path: string) => {
      if (path === workspaceDir) {
        setView('chat')
        return
      }
      try {
        const r = await anyGw()?.setWorkspace(path)
        if (!r) return
        setWorkspaceDir(r.workspace)
        void refreshProjects()
        void newSession()
      } catch {
        /* 忽略：连接波动时静默 */
      }
    },
    [anyGw, workspaceDir, refreshProjects, newSession],
  )

  // 对话框「创建」：登记（带自定义名）→ 切换为当前项目
  const createProject = useCallback(
    async (path: string, name: string) => {
      setShowNewProject(false)
      try {
        const r = await anyGw()?.addProject(path, name)
        if (r) setProjects(r.projects)
        await openProject(path)
      } catch {
        /* 目录不可用等：保持页面现状 */
      }
    },
    [anyGw, openProject],
  )

  const removeProjectFromList = useCallback(
    (path: string) => {
      anyGw()?.removeProject(path).then((r) => setProjects(r.projects)).catch(() => {})
    },
    [anyGw],
  )

  const renameProjectInList = useCallback(
    (path: string, name: string) => {
      anyGw()?.renameProject(path, name).then((r) => setProjects(r.projects)).catch(() => {})
    },
    [anyGw],
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

  const addFolder = useCallback(async () => {
    // 无活跃连接时不弹选择器，避免用户选完目录却被静默丢弃
    if (!connsRef.current[activeRef.current]) return
    const dir = await window.lumi.pickDirectory?.()
    if (dir) void applyFolderOp((gw) => gw.addFolder(dir))
  }, [applyFolderOp])

  const removeFolder = useCallback(
    (path: string) => void applyFolderOp((gw) => gw.removeFolder(path)),
    [applyFolderOp],
  )

  // 拉取任务列表：唯一数据源，侧栏分组与管理页共用（CRUD 后经 onRefresh 刷新）
  const refreshCronJobs = useCallback(() => {
    anyGw()
      ?.listCronJobs()
      .then((r) => {
        const jobs = r.jobs ?? []
        setCronJobs(jobs)
        // 回收已删任务的未读计数，避免 localStorage 残留
        setCronUnread((u) => {
          const ids = new Set(jobs.map((j) => j.id))
          const stale = Object.keys(u).filter((k) => !ids.has(k))
          if (stale.length === 0) return u
          const next = { ...u }
          for (const k of stale) delete next[k]
          return next
        })
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (conn === 'open') refreshCronJobs()
  }, [conn, cronVersion, refreshCronJobs])

  // 在任务会话视图内切换到某次执行的会话（不改变 view），并标记该次执行为已读。
  // 已读集合封顶 500 条（对象按插入序，砍最旧的），避免 localStorage 无限增长。
  const openRunThread = useCallback(
    async (tid: string) => {
      setCronRunThread(tid)
      setReadRuns((r) => {
        if (r[tid]) return r
        const next = { ...r, [tid]: true as const }
        const keys = Object.keys(next)
        for (const k of keys.slice(0, Math.max(0, keys.length - 500))) delete next[k]
        return next
      })
      await activate(tid)
    },
    [activate],
  )

  // 打开某任务的会话视图：默认选中最近一次有会话的执行
  const openCronJob = useCallback(
    async (jobId: string, threadId?: string) => {
      setView('cronjob')
      setActiveCronJob(jobId)
      setCronUnread((u) => (u[jobId] ? { ...u, [jobId]: 0 } : u))
      let tid = threadId
      if (!tid) {
        try {
          const r = await anyGw()?.listCronRuns(jobId)
          tid = r?.runs.find((x) => x.thread_id)?.thread_id
        } catch {
          /* 列表拉取失败时显示空态 */
        }
      }
      setCronRunThread(tid ?? null)
      if (tid) await openRunThread(tid)
    },
    [anyGw, openRunThread],
  )

  const pinSession = useCallback(
    (tid: string, pinned: boolean) => {
      anyGw()?.pinSession(tid, pinned).then(refreshSessions).catch(() => {})
    },
    [anyGw, refreshSessions],
  )

  const renameSession = useCallback(
    (tid: string, title: string) => {
      anyGw()?.renameSession(tid, title).then(refreshSessions).catch(() => {})
    },
    [anyGw, refreshSessions],
  )

  const deleteSession = async (session: SessionMeta) => {
    setPendingDelete(null)
    const tid = session.thread_id
    await anyGw()?.deleteSession(tid).catch(() => {})
    connsRef.current[tid]?.close()
    delete connsRef.current[tid]
    setStore((s) => {
      const n = { ...s }
      delete n[tid]
      return n
    })
    setFolderStore((s) => {
      if (!(tid in s)) return s
      const n = { ...s }
      delete n[tid]
      return n
    })
    // 删除的是当前会话：另开一个新会话顶上
    if (tid === activeRef.current) await activate(null)
    void refreshSessions()
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

  const send = () => {
    const text = input.trim()
    const imgs = attachments.filter((a) => a.kind === 'image')
    const fileRefs = attachments.filter((a) => a.kind === 'file')
    const gw = connsRef.current[active]
    if ((!text && attachments.length === 0) || running || !gw) return
    const files: AttachedFile[] = fileRefs.map((a) => ({ path: a.path, name: a.name }))
    setStore((s) => ({
      ...s,
      [active]: {
        ...s[active],
        items: [
          ...s[active].items,
          {
            id: nid(),
            kind: 'user',
            text, // 可见正文只留用户输入；附件路径走 system-reminder，不污染气泡
            images: imgs.length ? imgs.map((a) => a.dataUrl) : undefined,
            files: files.length ? files : undefined,
          },
        ],
        running: true,
      },
    }))
    setInput('')
    setAttachments([])
    // 纯文本的已知斜杠命令走 run_command；带附件则一律走 send_message
    if (attachments.length === 0 && text.startsWith('/')) {
      const [name, extra] = parseCommand(text)
      if (commands.some((c) => c.name === name)) {
        gw.runCommand(name, extra).catch(() => resetRunning(active))
        return
      }
    }
    if (imgs.length > 0 || files.length > 0) {
      const blocks: unknown[] = text ? [{ type: 'text', text }] : []
      // 图片拆为 Anthropic 原生图片块（后端按模型再转 OpenAI/Bedrock 格式）
      for (const a of imgs) {
        const m = /^data:([^;]+);base64,(.*)$/s.exec(a.dataUrl)
        if (m) blocks.push({ type: 'image', source: { type: 'base64', media_type: m[1], data: m[2] } })
      }
      // 文件路径以 <attached-file> 注入（display 层会剥离，不进可见正文），Agent 用 read 读取。
      // 标签名须与后端 constants.ATTACHED_FILE_TAG 一致（display 剥离 + 历史还原的单一事实源）。
      if (files.length > 0) {
        const lines = files.map((f) => `<attached-file>${f.path}</attached-file>`).join('\n')
        blocks.push({ type: 'text', text: lines })
      }
      gw.sendMessage(blocks).catch(() => resetRunning(active))
    } else {
      gw.sendMessage(text).catch(() => resetRunning(active))
    }
  }

  // 中止当前流式轮：后端取消 task 并补发 turn.complete，running 随之复位
  const stop = () => {
    connsRef.current[active]?.stop().catch(() => {})
  }

  const resumeWith = (value: unknown, clear: 'approval' | 'clarify' | 'plan') => {
    connsRef.current[active]?.resume(value).catch(() => resetRunning(active))
    setStore((s) => ({ ...s, [active]: { ...s[active], [clear]: null } }))
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
      send()
    }
  }

  const composer = (placeholder: string) => (
    <div>
      {menuOpen && (
        <CommandMenu
          commands={matched}
          selected={cmdSel}
          onPick={pickCommand}
          onHover={setCmdSel}
        />
      )}
      <div
        className="bg-surface rounded-3xl border border-line/40 focus-within:border-primary/40 transition-colors overflow-hidden"
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
        disabled={conn !== 'open'}
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
          <FolderMenu
            folders={folderStore[active] ?? []}
            onAdd={() => void addFolder()}
            onRemove={(p) => void removeFolder(p)}
          />
          <ModelPicker
            model={model}
            providers={providers}
            active={activeModel}
            onSwitch={switchModel}
            onSwitchEffort={switchEffort}
          />
        </div>
        <div className="flex items-center gap-1.5">
          <ContextMeter usage={cur?.ctx} window={contextWindow} model={model} />
          {running && !approval && !clarify && !plan ? (
            <Button
              size="icon"
              variant="destructive"
              onClick={stop}
              aria-label={t('composer.stop')}
              className="rounded-full"
            >
              <Square fill="currentColor" strokeWidth={0} className="size-3" />
            </Button>
          ) : (
            <Button
              size="icon"
              onClick={send}
              disabled={running || conn !== 'open' || (!input.trim() && attachments.length === 0)}
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

  return (
    <div className="h-full flex">
      <Sidebar
        sessions={sessions}
        currentThread={view === 'chat' ? active : ''}
        conn={conn}
        model={model}
        activity={activity}
        projectsActive={view === 'projects'}
        scheduledActive={view === 'scheduled'}
        cronJobs={cronJobs}
        cronUnread={cronUnread}
        cronRunning={cronRunning}
        activeCronJob={view === 'cronjob' ? activeCronJob : null}
        onOpenCronJob={openCronJob}
        onSelect={selectSession}
        onNew={newSession}
        onOpenProjects={openProjects}
        onOpenScheduled={openScheduled}
        onOpenSettings={openSettings}
        onPin={pinSession}
        onRename={renameSession}
        onDelete={setPendingDelete}
      />

      <main className="flex-1 flex flex-col min-w-0">
        <div className="h-9 app-drag shrink-0 flex items-center justify-end pr-3">
          {view === 'chat' && activeBgTasks.length > 0 && (
            <button
              onClick={() => setBgDrawerOpen((o) => !o)}
              title={t('bg.title')}
              className={`no-drag relative grid place-items-center w-7 h-7 rounded-lg transition-colors ${
                bgDrawerOpen
                  ? 'text-primary'
                  : 'text-muted-foreground hover:text-ink hover:bg-white/5'
              }`}
            >
              <PanelRight size={17} />
              {hasRunningBg && (
                <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
              )}
            </button>
          )}
        </div>
        {view === 'projects' ? (
          <ProjectsPage
            projects={projects}
            current={workspaceDir}
            onOpen={(p) => void openProject(p)}
            onNew={() => setShowNewProject(true)}
            onRemove={(path) =>
              setPendingRemoveProject(projects.find((p) => p.path === path) ?? null)
            }
            onRename={renameProjectInList}
          />
        ) : view === 'scheduled' ? (
          <CronPage
            api={anyGw}
            jobs={cronJobs}
            runningNames={cronRunning}
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
              ) : hasMessages ? (
                <>
                  <div ref={scrollRef} className="flex-1 overflow-auto">
                    <div className="max-w-3xl mx-auto w-full px-6 py-8 space-y-5">
                      {segments.map((seg) => {
                        const key = segKey(seg)
                        const node =
                          seg.kind === 'tools' ? (
                            <ToolGroup key={key} tools={seg.tools} />
                          ) : seg.kind === 'agent' ? (
                            <AgentGroup key={key} items={seg.items} />
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
                      {/* 状态指示器常驻：运行中显示阶段文案，中断（审批/澄清/计划）时
                          保持显示等待态，完成后退化为无文字的静止光点 */}
                      <StatusIndicator
                        items={items}
                        running={running}
                        waiting={!!(approval || clarify || plan)}
                        streaming={streaming}
                        thinkingText={thinkingText}
                      />
                    </div>
                  </div>
                  <div className="px-6 pb-5">
                    <div className="max-w-3xl mx-auto w-full">
                      {/* 审批/澄清/计划：渲染在输入框上方，切走时随会话留在原处 */}
                      {approval && <ApprovalDialog data={approval} onDecide={decide} />}
                      {clarify && (
                        <ClarifyDialog
                          data={clarify}
                          onSubmit={(answer) => resumeWith(answer, 'clarify')}
                          onCancel={() => resumeWith(ASK_CANCELLED, 'clarify')}
                        />
                      )}
                      {plan && (
                        <PlanDialog
                          data={plan}
                          onApprove={() => resumeWith('approved', 'plan')}
                          onReject={() => resumeWith(PLAN_REJECTED, 'plan')}
                        />
                      )}
                      {composer(t('composer.reply'))}
                    </div>
                  </div>
                </>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center px-6 -mt-8">
                  <div className="mb-8 flex items-center gap-2.5 select-none">
                    <span className="text-primary text-3xl">✦</span>
                    <span className="serif text-3xl">Lumi</span>
                  </div>
                  <div className="w-full max-w-2xl">{composer(t('composer.empty'))}</div>
                </div>
              )}
            </div>
            {view === 'cronjob' && activeCronJob && (
              <RunsRail
                api={anyGw}
                jobId={activeCronJob}
                activeThread={cronRunThread}
                readRuns={readRuns}
                version={cronVersion}
                onPick={(tid) => void openRunThread(tid)}
              />
            )}
            {view === 'chat' && (
              <BgTasksDrawer
                tasks={activeBgTasks}
                onStop={stopBgTask}
                onDismiss={dismissBgTask}
                onClearFinished={clearFinishedBgTasks}
                open={bgDrawerOpen}
              />
            )}
          </div>
        )}
      </main>

      {showSettings && (
        <SettingsDialog
          themePref={themePref}
          setThemePref={setThemePref}
          notify={notify}
          setNotify={toggleNotify}
          profiles={providers}
          active={activeModel}
          onSwitch={switchModel}
          onSave={saveProvider}
          onDelete={deleteProvider}
          onTest={testProvider}
          onClose={() => setShowSettings(false)}
        />
      )}
      {showNewProject && (
        <NewProjectDialog
          onCreate={(p, n) => void createProject(p, n)}
          onCancel={() => setShowNewProject(false)}
        />
      )}
      {pendingRemoveProject && (
        <ConfirmDialog
          title={t('projects.removeTitle')}
          message={t('projects.removeMessage', { name: pendingRemoveProject.name })}
          confirmLabel={t('projects.remove')}
          onConfirm={() => {
            removeProjectFromList(pendingRemoveProject.path)
            setPendingRemoveProject(null)
          }}
          onCancel={() => setPendingRemoveProject(null)}
        />
      )}
      {pendingDelete && (
        <ConfirmDialog
          title={t('confirm.deleteTitle')}
          message={t('confirm.deleteMessage', {
            name: pendingDelete.title || pendingDelete.first_message || t('sidebar.untitled'),
          })}
          onConfirm={() => deleteSession(pendingDelete)}
          onCancel={() => setPendingDelete(null)}
        />
      )}
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
}: {
  items: Item[]
  running: boolean
  waiting: boolean
  streaming: boolean
  thinkingText: string
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
        {item.text && (
          <div className="selectable bg-surface rounded-3xl rounded-br-lg px-4 py-2.5 max-w-[80%] whitespace-pre-wrap">
            {item.text}
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
      </div>
    )
  }
  if (item.kind === 'assistant') {
    return (
      <div className="md">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
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

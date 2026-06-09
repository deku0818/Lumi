import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Terminal,
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
  type LucideIcon,
} from 'lucide-react'
import { Gateway, type ConnState } from './gateway'
import type { HistoryItem, Item, SessionMeta, WireEvent } from './types'
import { ApprovalDialog } from './components/ApprovalDialog'
import { ClarifyDialog, ASK_CANCELLED } from './components/ClarifyDialog'
import { PlanDialog, PLAN_REJECTED } from './components/PlanDialog'
import { Sidebar } from './components/Sidebar'
import { ConfirmDialog } from './components/ConfirmDialog'
import { useTheme } from './theme'

// 单 app 实例，模块级自增 id 即可，避免 hook 依赖问题。
let _id = 0
const nid = () => ++_id

type ToolItem = Extract<Item, { kind: 'tool' }>
type Segment =
  | { kind: 'tools'; tools: ToolItem[] }
  | { kind: 'item'; item: Exclude<Item, { kind: 'tool' }> }

// 把连续的 tool item 合并成一段，其余 item 各自独立 —— 用于工具分组渲染
function groupItems(items: Item[]): Segment[] {
  const segs: Segment[] = []
  for (const it of items) {
    if (it.kind === 'tool') {
      const last = segs[segs.length - 1]
      if (last?.kind === 'tools') last.tools.push(it)
      else segs.push({ kind: 'tools', tools: [it] })
    } else {
      segs.push({ kind: 'item', item: it })
    }
  }
  return segs
}

// load_history 的历史项 → 前端 Item
function restore(h: HistoryItem): Item {
  if (h.kind === 'user') return { id: nid(), kind: 'user', text: h.text ?? '' }
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

// 每个会话的独立状态（多会话并发：A 在跑时可切到 B，互不影响）
type SessionState = {
  items: Item[]
  running: boolean
  approval: Record<string, unknown> | null
  clarify: Record<string, unknown> | null
  plan: Record<string, unknown> | null
}
const emptySession = (items: Item[] = []): SessionState => ({
  items,
  running: false,
  approval: null,
  clarify: null,
  plan: null,
})

export default function App() {
  const [store, setStore] = useState<Record<string, SessionState>>({})
  const [active, setActive] = useState('')
  const [conn, setConn] = useState<ConnState>('connecting')
  const [model, setModel] = useState('')
  const [input, setInput] = useState('')
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [pendingDelete, setPendingDelete] = useState<SessionMeta | null>(null)
  const [theme, toggleTheme] = useTheme()
  const connsRef = useRef<Record<string, Gateway>>({})
  const activeRef = useRef('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    activeRef.current = active
  }, [active])

  // 当前活动会话的派生视图
  const cur = store[active]
  const items = cur?.items ?? []
  const running = cur?.running ?? false
  const approval = cur?.approval ?? null
  const clarify = cur?.clarify ?? null
  const plan = cur?.plan ?? null

  // 按 session_id 路由事件到对应会话（后台会话的事件也能正确归位）
  const handleEvent = useCallback((ev: WireEvent) => {
    const { type, payload } = ev
    if (type === 'gateway.ready') {
      setModel((m) => m || payload.model || '')
      return
    }
    const sid = ev.session_id ?? ''
    setStore((store) => {
      const s = store[sid]
      if (!s) return store
      let n: SessionState | null = null
      switch (type) {
        case 'message.start':
          n = { ...s, items: [...s.items, { id: nid(), kind: 'assistant', text: '', streaming: true }] }
          break
        case 'message.delta':
          n = { ...s, items: appendDelta(s.items, payload.text ?? '') }
          break
        case 'message.complete':
          n = {
            ...s,
            items: s.items.map((it) =>
              it.kind === 'assistant' && it.streaming ? { ...it, streaming: false } : it,
            ),
          }
          break
        case 'tool.start':
          n = {
            ...s,
            items: [
              ...s.items,
              {
                id: nid(),
                kind: 'tool',
                toolCallId: payload.tool_call_id ?? '',
                name: payload.name ?? '',
                args: payload.args,
                output: '',
                done: false,
              },
            ],
          }
          break
        case 'tool.complete':
          n = {
            ...s,
            items: s.items.map((it) =>
              it.kind === 'tool' && it.toolCallId === payload.tool_call_id
                ? { ...it, output: payload.output ?? '', done: true }
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
          n = { ...s, running: false }
          break
        case 'error':
          n = { ...s, running: false, items: [...s.items, { id: nid(), kind: 'notice', text: payload.message }] }
          break
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
          gw.onEvent((ev) => {
            if (ev.type === 'gateway.ready') {
              setModel((m) => m || ev.payload.model || '')
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
  }, [items, running])

  useEffect(() => {
    if (conn === 'open' && !running) inputRef.current?.focus()
  }, [conn, running, active])

  const refreshSessions = useCallback(async () => {
    const gw = connsRef.current[activeRef.current] ?? Object.values(connsRef.current)[0]
    try {
      const r = await gw?.listSessions()
      if (r?.sessions) setSessions(r.sessions)
    } catch {
      /* 忽略：连接波动时静默 */
    }
  }, [])

  useEffect(() => {
    if (active) void refreshSessions()
  }, [active, running, refreshSessions])

  const newSession = async () => {
    const tid = await openConnection(null)
    setActive(tid)
    setConn('open')
    void refreshSessions()
  }

  const selectSession = async (tid: string) => {
    if (tid === active) return
    if (!connsRef.current[tid]) await openConnection(tid)
    setActive(tid)
    setConn('open')
  }

  // 会话管理 RPC 操作全局 checkpoint/元数据，与连接当前 thread 无关，任一活跃连接皆可。
  const anyGw = () =>
    connsRef.current[activeRef.current] ?? Object.values(connsRef.current)[0]

  const pinSession = (tid: string, pinned: boolean) => {
    anyGw()?.pinSession(tid, pinned).then(refreshSessions).catch(() => {})
  }

  const renameSession = (tid: string, title: string) => {
    anyGw()?.renameSession(tid, title).then(refreshSessions).catch(() => {})
  }

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
    // 删除的是当前会话：另开一个新会话顶上
    if (tid === activeRef.current) {
      const ntid = await openConnection(null)
      setActive(ntid)
      setConn('open')
    }
    void refreshSessions()
  }

  const send = () => {
    const text = input.trim()
    const gw = connsRef.current[active]
    if (!text || running || !gw) return
    setStore((s) => ({
      ...s,
      [active]: {
        ...s[active],
        items: [...s[active].items, { id: nid(), kind: 'user', text }],
        running: true,
      },
    }))
    setInput('')
    gw.sendMessage(text).catch(() => {})
  }

  const decide = (decision: 'approve' | 'reject') => {
    const value =
      decision === 'approve'
        ? { decision: 'approve' }
        : { decision: 'reject', message: '用户拒绝了此工具执行' }
    connsRef.current[active]?.resume(value).catch(() => {})
    setStore((s) => ({ ...s, [active]: { ...s[active], approval: null } }))
  }

  const resumeWith = (value: unknown, clear: 'clarify' | 'plan') => {
    connsRef.current[active]?.resume(value).catch(() => {})
    setStore((s) => ({ ...s, [active]: { ...s[active], [clear]: null } }))
  }

  const streaming = items.some((it) => it.kind === 'assistant' && it.streaming)
  const hasMessages = items.length > 0
  // 连续工具分段只随 items 变化重算，避免每次渲染都扫描
  const segments = useMemo(() => groupItems(items), [items])

  const composer = (placeholder: string) => (
    <div className="bg-surface rounded-3xl border border-line/40 focus-within:border-accent/40 transition-colors">
      <textarea
        ref={inputRef}
        value={input}
        rows={1}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            send()
          }
        }}
        disabled={conn !== 'open'}
        placeholder={placeholder}
        className="composer w-full bg-transparent resize-none max-h-48 px-4 pt-3.5 pb-1.5 outline-none placeholder:text-muted/50 disabled:opacity-50"
      />
      <div className="flex items-center justify-end gap-3 px-3 pb-2.5">
        <button
          onClick={send}
          disabled={running || conn !== 'open' || !input.trim()}
          aria-label="发送"
          className="size-8 rounded-full bg-accent text-canvas grid place-items-center hover:brightness-110 transition disabled:opacity-25 disabled:hover:brightness-100"
        >
          <span className="text-lg leading-none">↑</span>
        </button>
      </div>
    </div>
  )

  return (
    <div className="h-full flex">
      <Sidebar
        sessions={sessions}
        currentThread={active}
        conn={conn}
        model={model}
        theme={theme}
        disabled={false}
        onSelect={selectSession}
        onNew={newSession}
        onToggleTheme={toggleTheme}
        onPin={pinSession}
        onRename={renameSession}
        onDelete={setPendingDelete}
      />

      <main className="flex-1 flex flex-col min-w-0">
        <div className="h-9 app-drag shrink-0" />
        {hasMessages ? (
          <>
            <div ref={scrollRef} className="flex-1 overflow-auto">
              <div className="max-w-3xl mx-auto w-full px-6 py-8 space-y-5">
                {segments.map((seg) =>
                  seg.kind === 'tools' ? (
                    <ToolGroup key={`g${seg.tools[0].id}`} tools={seg.tools} />
                  ) : (
                    <ItemView key={seg.item.id} item={seg.item} />
                  ),
                )}
                {running && !streaming && <Thinking />}
              </div>
            </div>
            <div className="px-6 pb-5">
              <div className="max-w-3xl mx-auto w-full">{composer('回复 Lumi…')}</div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center px-6 -mt-8">
            <div className="mb-8 flex items-center gap-2.5 select-none">
              <span className="text-accent text-3xl">✦</span>
              <span className="serif text-3xl">Lumi</span>
            </div>
            <div className="w-full max-w-2xl">{composer('有什么可以帮你的？')}</div>
          </div>
        )}
      </main>

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
      {pendingDelete && (
        <ConfirmDialog
          title="删除对话"
          message={`确定删除「${pendingDelete.title || pendingDelete.first_message || '新对话'}」？此操作不可恢复。`}
          onConfirm={() => deleteSession(pendingDelete)}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  )
}

function Thinking() {
  return (
    <div className="flex items-center gap-2 text-muted text-sm">
      <span className="text-accent animate-pulse">✦</span>
      <span className="animate-pulse">正在思考…</span>
    </div>
  )
}

function ItemView({ item }: { item: Exclude<Item, { kind: 'tool' }> }) {
  if (item.kind === 'user') {
    return (
      <div className="flex justify-end">
        <div className="bg-surface rounded-3xl rounded-br-lg px-4 py-2.5 max-w-[80%] whitespace-pre-wrap">
          {item.text}
        </div>
      </div>
    )
  }
  if (item.kind === 'assistant') {
    return (
      <div className="group">
        <div className="md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
          {item.streaming && <span className="cursor">▋</span>}
        </div>
        {!item.streaming && item.text && (
          <div className="mt-1 -ml-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <CopyButton text={item.text} />
          </div>
        )}
      </div>
    )
  }
  return (
    <div className="text-sm text-error/80 bg-error/5 rounded-xl px-3.5 py-2.5">{item.text}</div>
  )
}

// AI 消息下的复制按钮：悬停出现，点击复制 markdown 原文，1.5s 内显示「已复制」反馈。
function CopyButton({ text }: { text: string }) {
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
    <button
      onClick={copy}
      title={copied ? '已复制' : '复制'}
      aria-label="复制"
      className="size-7 grid place-items-center rounded-md text-muted hover:bg-surface hover:text-ink transition"
    >
      {copied ? <Check size={15} className="text-success" /> : <Copy size={15} />}
    </button>
  )
}

// 工具（单个或多个）统一渲染为一行自然语言摘要（参考 Claude：
// "Edited 2 files, ran a command, read a file ›"）。无卡片、低调融入文本流，
// 点击展开看每个工具的细节。运行中强制展开看进度，完成后默认折叠。
function ToolGroup({ tools }: { tools: ToolItem[] }) {
  const running = tools.some((t) => !t.done)
  const [manualOpen, setManualOpen] = useState(false)
  const open = running || manualOpen
  const summary = running
    ? `${summarizeTools(tools.filter((t) => t.done)) || 'Working'}…`
    : summarizeTools(tools)

  return (
    <div>
      <button
        onClick={() => setManualOpen((o) => !o)}
        className="flex items-center gap-1.5 text-sm text-muted hover:text-ink transition"
      >
        {running && <span className="text-accent animate-pulse text-[10px]">●</span>}
        <span>{summary}</span>
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
}

// 展开后的工具明细行：图标 + 人类可读标题 + 旋转箭头，点击看输出
function ToolRow({ item }: { item: ToolItem }) {
  const [open, setOpen] = useState(false)
  const hasOutput = item.done && !!item.output
  const Icon = toolIcon(item.name)
  return (
    <div className="rounded-lg overflow-hidden">
      <button
        onClick={() => hasOutput && setOpen((o) => !o)}
        className={`w-full px-2 py-1.5 flex items-center gap-2.5 text-left text-sm rounded-lg ${hasOutput ? 'hover:bg-white/5' : 'cursor-default'}`}
      >
        <Icon
          size={15}
          className={`shrink-0 ${item.done ? 'text-muted' : 'text-accent animate-pulse'}`}
        />
        <span className="truncate flex-1 text-ink/80">{toolTitle(item.name, item.args)}</span>
        {hasOutput && (
          <ChevronRight
            size={13}
            className={`shrink-0 text-muted transition-transform ${open ? 'rotate-90' : ''}`}
          />
        )}
      </button>
      {open && hasOutput && (
        <pre className="text-xs text-muted/90 ml-[26px] mr-1 mb-1 px-3 py-2 rounded-lg bg-canvas/60 overflow-auto max-h-60 whitespace-pre-wrap">
          {item.output.slice(0, 4000)}
          {item.output.length > 4000 && '\n…（已截断）'}
        </pre>
      )}
    </div>
  )
}

// 文本提取小工具（toolTitle 标题提取共用）
const argStr = (v: unknown) => (typeof v === 'string' ? v : '')
const clip = (s: string, n = 72) => (s.length > n ? s.slice(0, n) + '…' : s)
const basename = (p: string) => p.split('/').filter(Boolean).pop() || p

// 每个工具的展示元数据（图标 + 动作动词/名词 + 人类可读标题提取）集中在一张表，
// 新增工具只需加一行。icon 驱动 ToolRow 图标，verb/noun 驱动 summarizeTools 聚合，
// title 从 args 提取非技术用户看得懂的标题。
type ToolMeta = {
  icon: LucideIcon
  verb: string
  noun: string
  title: (a: Record<string, unknown>, name: string) => string
}
const fileTitle = (a: Record<string, unknown>, name: string) =>
  argStr(a.file_path) ? basename(argStr(a.file_path)) : name
const searchTitle = (a: Record<string, unknown>) =>
  argStr(a.pattern) ? `Search ${clip(argStr(a.pattern), 48)}` : 'Search'

const TOOL_META: Record<string, ToolMeta> = {
  bash: { icon: Terminal, verb: 'Ran', noun: 'command', title: (a) => clip(argStr(a.description) || argStr(a.command) || 'Run command') },
  read: { icon: FileText, verb: 'Read', noun: 'file', title: fileTitle },
  write: { icon: FilePlus, verb: 'Wrote', noun: 'file', title: fileTitle },
  edit: { icon: FilePen, verb: 'Edited', noun: 'file', title: fileTitle },
  grep: { icon: Search, verb: 'Searched', noun: '', title: searchTitle },
  glob: { icon: Search, verb: 'Searched', noun: '', title: searchTitle },
  agent: { icon: Bot, verb: 'Ran', noun: 'subagent', title: (a) => clip(argStr(a.prompt) || argStr(a.name) || 'Run subagent') },
  todo: { icon: ListChecks, verb: 'Updated', noun: 'todo', title: () => 'Update todos' },
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
  const a = (args && typeof args === 'object' ? args : {}) as Record<string, unknown>
  const m = TOOL_META[name]
  if (m) return m.title(a, name)
  const first = Object.values(a).find((v) => typeof v === 'string')
  return first ? clip(String(first)) : name
}

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
  Square,
  Plus,
  X,
  type LucideIcon,
} from 'lucide-react'
import { Gateway, type ConnState } from './gateway'
import type {
  ActiveModel,
  HistoryItem,
  Item,
  ProviderProfile,
  SessionMeta,
  SlashCommand,
  WireEvent,
} from './types'
import { ApprovalDialog } from './components/ApprovalDialog'
import { ClarifyDialog, ASK_CANCELLED } from './components/ClarifyDialog'
import { PlanDialog, PLAN_REJECTED } from './components/PlanDialog'
import { Sidebar } from './components/Sidebar'
import { ConfirmDialog } from './components/ConfirmDialog'
import { SettingsDialog } from './components/SettingsDialog'
import { ModelPicker } from './components/ModelPicker'
import { CommandMenu } from './components/CommandMenu'
import { Composer } from './components/Composer'
import { isCommandMode, parseCommand, matchCommands } from './slash'
import { toolDiff, type DiffLine } from './diff'
import { Button } from '@/components/ui/button'
import { useTheme } from './theme'
import { useI18n } from './i18n'

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
  if (h.kind === 'user') return { id: nid(), kind: 'user', text: h.text ?? '', images: h.images }
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
  const [commands, setCommands] = useState<SlashCommand[]>([])
  const [cmdSel, setCmdSel] = useState(0)
  const [cmdDismissed, setCmdDismissed] = useState(false)
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [providers, setProviders] = useState<ProviderProfile[]>([])
  const [activeModel, setActiveModel] = useState<ActiveModel>({ provider: '', model: '' })
  const [showSettings, setShowSettings] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<SessionMeta | null>(null)
  const [themePref, setThemePref] = useTheme()
  const { t } = useI18n()
  const [notify, setNotify] = useState(() => localStorage.getItem('lumi-notify') === '1')
  const [attachments, setAttachments] = useState<{ id: number; dataUrl: string }[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)
  const connsRef = useRef<Record<string, Gateway>>({})
  const activeRef = useRef('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // handleEvent 是 []-依赖的稳定回调，通过 ref 读取最新的 store / 通知开关 / 翻译
  const storeRef = useRef<Record<string, SessionState>>({})
  const notifyRef = useRef(notify)
  const tRef = useRef(t)

  useEffect(() => {
    activeRef.current = active
  }, [active])

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
  const approval = cur?.approval ?? null
  const clarify = cur?.clarify ?? null
  const plan = cur?.plan ?? null

  useEffect(() => {
    storeRef.current = store
  }, [store])
  useEffect(() => {
    notifyRef.current = notify
  }, [notify])
  useEffect(() => {
    tRef.current = t
  })

  // 每个会话的活动态，喂给侧栏显示圆点：attention=等你处理（审批/澄清/计划），running=处理中
  const activity = useMemo(() => {
    const m: Record<string, 'running' | 'attention'> = {}
    for (const tid in store) {
      const s = store[tid]
      if (s.approval || s.clarify || s.plan) m[tid] = 'attention'
      else if (s.running) m[tid] = 'running'
    }
    return m
  }, [store])

  // 按 session_id 路由事件到对应会话（后台会话的事件也能正确归位）
  const handleEvent = useCallback((ev: WireEvent) => {
    const { type, payload } = ev
    if (type === 'gateway.ready') {
      setModel((m) => m || payload.model || '')
      return
    }
    const sid = ev.session_id ?? ''
    // 回复完成且开启通知：仅在该会话非当前活动、或窗口未聚焦时弹系统通知（避免你正盯着时打扰）。
    // 用 hasFocus 而非 document.hidden（切到别的应用时窗口仍可见，hidden 恒为 false）；
    // 通知经主进程发出（renderer 的 HTML5 Notification 在 macOS dev 下不可靠），
    // 点击由 onNotifyClick 回调切会话。
    if (type === 'turn.complete') {
      window.lumi.log?.(
        `turn.complete sid=${sid} active=${activeRef.current} notify=${notifyRef.current} hasFocus=${document.hasFocus()}`,
      )
    }
    // 系统通知：回复完成 + 等待用户处理的中断（审批/提问/计划）。
    // 仅在该会话非当前活动、或窗口未聚焦时弹（你正盯着时不打扰）。
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
      switch (type) {
        // message.start 不再预建空 assistant：模型直接调工具（无文字）时会留下空气泡，
        // 还会把相邻工具在 groupItems 里隔断。改由首个 message.delta 懒创建气泡。
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
          let ready = false
          gw.onEvent((ev) => {
            if (ev.type === 'gateway.ready') {
              setModel((m) => m || ev.payload.model || '')
              if (ready) {
                // 重连：服务端给的是全新 bridge（新 session_id），切回本连接原 thread
                // 恢复后端绑定，否则会丢弃原会话、并多出一个幽灵空会话。
                if (myThread) void gw.switchSession(myThread)
                return
              }
              ready = true
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

  // 拉取斜杠命令（技能命令，按项目动态）。技能目录随项目变化，故进入命令模式时刷新。
  const loadCommands = useCallback(() => {
    const gw = connsRef.current[activeRef.current] ?? Object.values(connsRef.current)[0]
    gw?.listCommands()
      .then((r) => setCommands(r.commands ?? []))
      .catch(() => {})
  }, [])

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
    const gw = connsRef.current[activeRef.current] ?? Object.values(connsRef.current)[0]
    gw?.listProviders().then(applyProviderResp).catch(() => {})
  }, [applyProviderResp])

  useEffect(() => {
    if (active) loadProviders()
  }, [active, loadProviders])

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

  // 读取图片文件为 data URL 加入附件（粘贴 / 拖拽 / ＋ 选择 共用，仅图片类型）
  const addImages = (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      if (!f.type.startsWith('image/')) continue
      const reader = new FileReader()
      reader.onload = () => {
        if (typeof reader.result === 'string') {
          const url = reader.result
          setAttachments((a) => [...a, { id: nid(), dataUrl: url }])
        }
      }
      reader.readAsDataURL(f)
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
      addImages(files)
    }
  }

  const onDropImages = (e: React.DragEvent) => {
    if (e.dataTransfer?.files?.length) {
      e.preventDefault()
      addImages(e.dataTransfer.files)
    }
  }

  const removeImage = (id: number) => setAttachments((a) => a.filter((x) => x.id !== id))

  const send = () => {
    const text = input.trim()
    const imgs = attachments
    const gw = connsRef.current[active]
    if ((!text && imgs.length === 0) || running || !gw) return
    setStore((s) => ({
      ...s,
      [active]: {
        ...s[active],
        items: [
          ...s[active].items,
          { id: nid(), kind: 'user', text, images: imgs.length ? imgs.map((a) => a.dataUrl) : undefined },
        ],
        running: true,
      },
    }))
    setInput('')
    setAttachments([])
    // 纯文本的已知斜杠命令走 run_command；带图片则一律走多模态 send_message
    if (imgs.length === 0 && text.startsWith('/')) {
      const [name, extra] = parseCommand(text)
      if (commands.some((c) => c.name === name)) {
        gw.runCommand(name, extra).catch(() => {})
        return
      }
    }
    if (imgs.length > 0) {
      // 拆 data URL 为 Anthropic 原生图片块（后端按模型再转 OpenAI/Bedrock 格式）
      const blocks: unknown[] = text ? [{ type: 'text', text }] : []
      for (const a of imgs) {
        const m = /^data:([^;]+);base64,(.*)$/s.exec(a.dataUrl)
        if (m) blocks.push({ type: 'image', source: { type: 'base64', media_type: m[1], data: m[2] } })
      }
      gw.sendMessage(blocks).catch(() => {})
    } else {
      gw.sendMessage(text).catch(() => {})
    }
  }

  // 中止当前流式轮：后端取消 task 并补发 turn.complete，running 随之复位
  const stop = () => {
    connsRef.current[active]?.stop().catch(() => {})
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
        className="bg-surface rounded-3xl border border-line/40 focus-within:border-primary/40 transition-colors"
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDropImages}
      >
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 px-3.5 pt-3">
          {attachments.map((a) => (
            <div key={a.id} className="relative group/att">
              <img
                src={a.dataUrl}
                alt=""
                className="size-16 object-cover rounded-xl border border-line/40"
              />
              <button
                onClick={() => removeImage(a.id)}
                aria-label={t('composer.removeImage')}
                className="absolute -top-1.5 -right-1.5 size-5 grid place-items-center rounded-full bg-canvas border border-line text-muted hover:text-ink opacity-0 group-hover/att:opacity-100 transition"
              >
                <X size={12} />
              </button>
            </div>
          ))}
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
            className="text-muted"
          >
            <Plus />
          </Button>
          <ModelPicker
            model={model}
            providers={providers}
            active={activeModel}
            onSwitch={switchModel}
          />
        </div>
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
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) addImages(e.target.files)
          e.target.value = ''
        }}
      />
    </div>
  )

  return (
    <div className="h-full flex">
      <Sidebar
        sessions={sessions}
        currentThread={active}
        conn={conn}
        model={model}
        activity={activity}
        disabled={false}
        onSelect={selectSession}
        onNew={newSession}
        onOpenSettings={() => setShowSettings(true)}
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
                {/* 审批/澄清/计划：会话内内联渲染，切走时随会话留在原处（不再是全局遮罩） */}
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
                {running && !streaming && !approval && !clarify && !plan && <Thinking />}
              </div>
            </div>
            <div className="px-6 pb-5">
              <div className="max-w-3xl mx-auto w-full">{composer(t('composer.reply'))}</div>
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

function Thinking() {
  const { t } = useI18n()
  return (
    <div className="flex items-center gap-2 text-muted text-sm">
      <span className="text-primary animate-pulse">✦</span>
      <span className="animate-pulse">{t('common.thinking')}</span>
    </div>
  )
}

function ItemView({ item }: { item: Exclude<Item, { kind: 'tool' }> }) {
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
          <div className="bg-surface rounded-3xl rounded-br-lg px-4 py-2.5 max-w-[80%] whitespace-pre-wrap">
            {item.text}
          </div>
        )}
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
      className="text-muted"
    >
      {copied ? <Check className="text-success" /> : <Copy />}
    </Button>
  )
}

// 工具（单个或多个）统一渲染为一行自然语言摘要（参考 Claude：
// "Edited 2 files, ran a command, read a file ›"）。无卡片、低调融入文本流，
// 点击展开看每个工具的细节。运行中强制展开看进度，完成后默认折叠。
function ToolGroup({ tools }: { tools: ToolItem[] }) {
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
        className="flex items-center gap-1.5 text-sm text-muted hover:text-ink transition"
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
}

// 展开后的工具明细行：图标 + 人类可读标题 + 旋转箭头，点击看输出/diff。
// 出错的工具行红色高亮并默认展开；edit/write 渲染 +/- diff 而非裸输出。
function ToolRow({ item }: { item: ToolItem }) {
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
          className={`shrink-0 ${!item.done ? 'text-primary animate-pulse' : errored ? 'text-error' : 'text-muted'}`}
        />
        <span className={`truncate flex-1 ${errored ? 'text-error' : 'text-ink/80'}`}>
          {toolTitle(item.name, item.args)}
        </span>
        {hasDetail && (
          <ChevronRight
            size={13}
            className={`shrink-0 text-muted transition-transform ${open ? 'rotate-90' : ''}`}
          />
        )}
      </button>
      {open && diff && <DiffView lines={diff} />}
      {open && !diff && hasOutput && (
        <pre
          className={`text-xs ml-[26px] mr-1 mb-1 px-3 py-2 rounded-lg bg-canvas/60 overflow-auto max-h-60 whitespace-pre-wrap ${errored ? 'text-error/90' : 'text-muted/90'}`}
        >
          {item.output.slice(0, 4000)}
          {item.output.length > 4000 && '\n' + t('common.truncated')}
        </pre>
      )}
    </div>
  )
}

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
            className={`select-none ${l.kind === 'add' ? 'text-success' : l.kind === 'del' ? 'text-error' : 'text-muted/40'}`}
          >
            {l.kind === 'add' ? '+ ' : l.kind === 'del' ? '- ' : '  '}
          </span>
          <span className={l.kind === 'ctx' ? 'text-muted/70' : 'text-ink/90'}>{l.text || ' '}</span>
        </div>
      ))}
    </pre>
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

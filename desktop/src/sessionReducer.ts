// 会话状态与事件归约。store = { [sessionKey]: SessionState }，多会话并发：A 在跑时可切到 B，
// 互不影响。本文件只有纯函数——系统通知 / toast 等副作用在 hooks/useEventNotifications。
import type {
  ActiveModel,
  AttachedFile,
  HistoryItem,
  HistorySnapshot,
  Item,
  SessionModelWire,
  TodoItem,
  ToolItem,
  Usage,
  WireEvent,
  WireEventPayloads,
} from './types'
import type { CtxUsage } from './components/ContextMeter'

// 单 app 实例，模块级自增 id 即可，避免 hook 依赖问题。
let _id = 0
export const nid = () => ++_id


// 每个会话的独立状态
export type SessionState = {
  items: Item[]
  running: boolean
  // 本轮开始的墙钟时间（epoch 毫秒）：计时由此推算，切走再切回 / 重连都不归零
  runStart?: number
  // 当前进行中的思考流文本（只在思考期间非空；正文/工具一开始即清空，不留痕迹）
  thinkingText: string
  // 挂起的审批/澄清按 approval_id 排队（后端并发解锁：一条消息多个工具 / 多个前台子代理
  // 可同时挂起审批）；渲染队首，逐个应答出队，不丢任何挂起的 Future。
  approval: WireEventPayloads['approval.request'][]
  clarify: WireEventPayloads['clarify.request'][]
  // 最近一次模型调用的上下文用量（用于输入栏的上下文进度环）；首轮前为 undefined
  ctx?: CtxUsage
  // 渠道旁观会话的上下文环分母：会话真实模型名与其窗口（旁观连接拿不到该会话的
  // 运行时模型，由 load_history 快照带出）；desktop 自己的会话不用（直接取 sessionModel）
  ctxModel?: string
  ctxWindow?: number
  // 本会话**已固化**的模型（后端 switch_session / set_session_model 下发）。未固化时
  // 留空，由 sessionModel 落到 defaultModel——固化与否是后端的判断，前端只存结果
  model?: ActiveModel
  // 历史压缩进行中（Summarizer 内部摘要调用期间为 true）；展示「正在压缩对话」指示
  compacting?: boolean
  // todos 工具的任务列表快照（右栏任务进度节）；空/未定义 = 节不渲染
  todos?: TodoItem[]
  // 本次模型调用开始时的 items 长度：message.retry 据此只回滚这一次调用流出的气泡，
  // 不误伤同一轮里更早迭代（已落库）的助手文字。message.start 每次调用都刷新。
  streamMark?: number
}
type Store = Record<string, SessionState>

export const emptySession = (items: Item[] = []): SessionState => ({
  items,
  running: false,
  thinkingText: '',
  approval: [],
  clarify: [],
})

// load_history 的历史项 → 前端 Item
function restore(h: HistoryItem): Item {
  if (h.kind === 'user')
    return { id: nid(), kind: 'user', text: h.text ?? '', images: h.images, files: h.files, sender: h.sender, ts: h.ts, messageId: h.message_id }
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

// 乐观插入的用户气泡（发送 / 编辑重发共用）。messageId 留空，等 turn.start 上锚。
// ts 与服务端落库的到达时刻近似一致，重载前后时间头不跳变。
export const userBubble = (text: string, images?: string[], files?: AttachedFile[]): Item => ({
  id: nid(),
  kind: 'user',
  text,
  images,
  files,
  ts: Date.now(),
})

// 给最后一条尚未上锚的用户气泡打上后端消息 id（turn.start 事件驱动）。
// 已上锚（历史回放带 id）或本轮无用户气泡（系统命令）时原数组返回，不触发重渲染。
function anchorLastUser(items: Item[], messageId: string): Item[] {
  if (!messageId) return items
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind !== 'user') continue
    if (it.messageId) return items
    const copy = items.slice()
    copy[i] = { ...it, messageId }
    return copy
  }
  return items
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

// 轮次收尾：结束流式气泡 + 清掉只活在本轮的重试提示（complete/error 共用）。
// 轮收尾：落定气泡 + 清掉可能残留的审批/澄清弹窗（stop / 切会话把挂起审批以拒绝收尾时
// 不经 decide/resume 清理，靠这里兜底）。turn.complete 与 error 共用。
const endTurn = (s: SessionState): SessionState => ({
  ...s,
  running: false,
  compacting: false,
  approval: [],
  clarify: [],
  items: finishTurn(s.items),
})

function finishTurn(items: Item[]): Item[] {
  return finishStreaming(items.filter((it) => it.kind !== 'retry'))
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
    // usage 按 max 累计
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

// 按 approval_id 把挂起的审批/澄清入队；已在队列则原样返回（重连后端会重发，去重保幂等）。
function enqueuePending<T extends { approval_id: string }>(queue: T[], item: T): T[] {
  return queue.some((p) => p.approval_id === item.approval_id) ? queue : [...queue, item]
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
export const hasStreaming = (s: SessionState): boolean =>
  s.items.some((it) => it.kind === 'assistant' && it.streaming)

// loadHistory 结果 → 会话槽位的统一水合（初次加载 / 重连补拉 / 渠道切回三处共用）。
// 已有流式在途内容时保留现有 items 不覆盖：checkpoint 快照比正在流出的直播轮旧，
// 整体替换会截断刚流入的助手内容/工具卡。调用方置 loaded 前须自查 hasStreaming——
// 快照被丢弃时置 loaded 会把掉线前的历史永久关在补拉门外。
export function hydrateHistory(s: SessionState, r: HistorySnapshot): SessionState {
  return {
    ...s,
    items: hasStreaming(s) ? s.items : r.items.map(restore),
    // todos 不套 items 的 hasStreaming 护栏：它是全量替换语义的 state 快照，由触发
    // todos.update 的同一个 Command 原子写入，永不比已收到的事件旧；重连补拉时反而
    // 更新（gap 期错过的 todos 更新只能靠这份快照补回，turn.complete 不带 todos）。
    todos: r.todos ?? s.todos,
    ctx: ctxFromUsage(r.usage) ?? s.ctx,
    // 渠道旁观会话的上下文环分母来源（会话真实模型窗口）；desktop 自己的会话此值虽也回填但不消费。
    // 模型名与窗口成对更新：窗口未知（0，如目录查不到的模型）时整对保旧，避免明细弹窗
    // 出现「新模型名 · 旧模型窗口」的错配。
    ...(r.context_window ? { ctxModel: r.model, ctxWindow: r.context_window } : {}),
  }
}

// 后端下发的会话模型 → 会话槽位补丁（switch_session / set_session_model 共用）。
// **只存已固化的**：未固化的会话在后端跟随「新会话默认」，前端把 model 留空，
// sessionModel 便自动 fallback 到 defaultModel——改默认时零 RPC 自动同步，
// 也不会出现「显示冻在握手那一刻、实跑跟着默认走」的脱节
export const sessionModelPatch = (m: SessionModelWire): Partial<SessionState> => ({
  model: m.pinned ? { model: m.model, provider: m.provider } : undefined,
})

// 按会话归位的流式事件（gateway.ready / 进程级广播不到这里）。
// 子代理的逐字流（正文/思考）不进 UI——只把子工具调用与 token 用量归属到父 agent 卡片
// （applyChildEvent）。中断类（审批/澄清/计划）即便带 parent_run_id 也照常往下走，仍需用户处理。
export function reduceEvent(store: Store, sid: string, ev: WireEvent, defaultModel: ActiveModel): Store {
  const s = store[sid]
  if (!s) return store
  const { type, payload } = ev
  const parentRun = payload.parent_run_id ?? ''
  if (parentRun && (type === 'message.delta' || type === 'message.start' || type === 'message.retry' || type === 'thinking.delta')) {
    return store
  }
  if (parentRun && (type === 'tool.start' || type === 'tool.complete' || type === 'message.complete')) {
    return { ...store, [sid]: applyChildEvent(s, parentRun, type, payload) ?? s }
  }
  let n: SessionState | null = null
  switch (ev.type) {
    // message.start 不再预建空 assistant：模型直接调工具（无文字）时会留下空气泡，
    // 还会把相邻工具在 groupItems 里隔断。改由首个 message.delta 懒创建气泡。
    case 'message.delta':
      n = { ...s, items: appendDelta(s.items, ev.payload.text ?? '') }
      break
    case 'turn.start':
      // 开轮广播本轮用户消息 id：给最后一条尚未上锚的用户气泡补上（run.lock 保证
      // 每会话同时只有一轮在飞，故「最后一条无 messageId 的用户气泡」无歧义）。
      // 时间旅行按此 id 截断，不做序号/文本猜测。
      // 同时固化会话模型：真人轮开跑正是后端 session_model.pin 的时刻，两边同时
      // 发生——不同步的话，本会话此后切模型不会弹缓存失效确认
      n = {
        ...s,
        items: anchorLastUser(s.items, ev.payload.message_id ?? ''),
        model: s.model ?? defaultModel,
      }
      break
    case 'thinking.delta':
      n = { ...s, thinkingText: s.thinkingText + (ev.payload.text ?? '') }
      break
    case 'compaction.status':
      // 历史压缩进行中：仅切状态，不进消息流（摘要全文由后端拦截，不会泄漏为助手回答）
      n = { ...s, compacting: !!ev.payload.active }
      break
    case 'message.start':
      // 每次模型调用的流起点。retry 要回滚的正是本次调用之后追加的气泡，而
      // 畸形那次调用的 message.complete 先于 message.retry 到达（已把 streaming
      // 清成 false），故必须在此记边界，不能事后靠 streaming 反推。
      n = { ...s, streamMark: s.items.length }
      break
    case 'message.complete':
      n = { ...s, items: finishStreaming(s.items), ctx: ctxFromUsage(ev.payload.usage) ?? s.ctx }
      break
    case 'message.retry':
      // 后端丢弃了畸形响应重试：回滚本次调用流出的气泡，原位留一行提示
      //（思考文本由下方统一收口清掉）
      n = {
        ...s,
        items: [...s.items.slice(0, s.streamMark ?? s.items.length), { id: nid(), kind: 'retry' }],
      }
      break
    case 'tool.start': {
      const tcid = ev.payload.tool_call_id ?? ''
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
            name: ev.payload.name ?? '',
            args: ev.payload.args,
            output: '',
            done: false,
            // agent 工具自带 run_id：子工具事件经 parent_run_id 归属到此卡片
            ...(ev.payload.run_id ? { runId: ev.payload.run_id, children: [] } : {}),
          },
        ],
      }
      break
    }
    case 'tool.complete': {
      const p = ev.payload
      n = {
        ...s,
        items: s.items.map((it) =>
          it.kind === 'tool' && it.toolCallId === p.tool_call_id
            ? { ...it, output: p.output ?? '', done: true, error: !!p.is_error }
            : it,
        ),
      }
      break
    }
    case 'approval.request': {
      // 追加而非覆盖：并发审批各自入队、逐个处理，不丢任何挂起的 Future（去重见 enqueuePending）
      const q = enqueuePending(s.approval, ev.payload)
      n = q === s.approval ? s : { ...s, approval: q }
      break
    }
    case 'clarify.request': {
      const q = enqueuePending(s.clarify, ev.payload)
      n = q === s.clarify ? s : { ...s, clarify: q }
      break
    }
    case 'todos.update':
      n = { ...s, todos: ev.payload.todos ?? [] }
      break
    case 'turn.complete':
      n = { ...endTurn(s), ctx: ctxFromUsage(ev.payload.usage) ?? s.ctx }
      break
    case 'error': {
      // 出错中断的流（bridge 只发 error、无 message.complete）也要收尾气泡 + 关弹窗
      const ended = endTurn(s)
      n = {
        ...ended,
        items: [...ended.items, { id: nid(), kind: 'notice', text: ev.payload.message }],
      }
      break
    }
  }
  // 思考的生命周期统一收口：除 thinking.delta 自身外，任何事件到达都意味着
  // 这段思考已结束（正文/工具/审批/轮次边界），清空累积文本——新增事件类型
  // 无需再各自记得清理
  if (n && type !== 'thinking.delta' && n.thinkingText) {
    n = { ...n, thinkingText: '' }
  }
  return n ? { ...store, [sid]: n } : store
}

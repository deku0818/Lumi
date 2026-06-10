// WS JSON-RPC 客户端：对接 lumi serve 的 /ws。
// 帧协议见 lumi/server/ws.py。带指数退避自动重连（sidecar 启动需要时间）。
import type {
  ActiveModel,
  CronJob,
  CronRun,
  HistoryItem,
  ProviderProfile,
  SessionMeta,
  SlashCommand,
  WireEvent,
} from './types'

export type ConnState = 'connecting' | 'open' | 'closed'

type EventHandler = (ev: WireEvent) => void
type StateHandler = (s: ConnState) => void
type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void }

export class Gateway {
  private ws: WebSocket | null = null
  private nextId = 1
  private pending = new Map<number, Pending>()
  private eventHandlers = new Set<EventHandler>()
  private stateHandlers = new Set<StateHandler>()
  private retry = 0
  private closedByUser = false

  constructor(private readonly url: string) {}

  connect(): void {
    this.closedByUser = false
    this.setState('connecting')
    const ws = new WebSocket(this.url)
    this.ws = ws
    ws.onopen = () => {
      this.retry = 0
      this.setState('open')
    }
    ws.onclose = () => {
      this.setState('closed')
      // 连接断开：在飞的 RPC 不会再有响应，全部 reject 避免调用方永久挂起。
      // 关键如 send_message——否则其 Promise 永不 settle，且新连接不补发 turn.complete，
      // 会话会卡在 running 态、输入框永久禁用。
      this.flushPending(new Error('连接已断开'))
      if (!this.closedByUser) {
        const delay = Math.min(8000, 500 * 2 ** Math.min(this.retry++, 4))
        setTimeout(() => this.connect(), delay)
      }
    }
    ws.onmessage = (e) => this.onMessage(JSON.parse(e.data))
  }

  private onMessage(frame: any): void {
    if (frame.method === 'event') {
      for (const h of this.eventHandlers) h(frame.params)
    } else if (frame.id != null) {
      const p = this.pending.get(frame.id)
      if (p) {
        this.pending.delete(frame.id)
        frame.error ? p.reject(frame.error) : p.resolve(frame.result)
      }
    }
  }

  request(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    return new Promise((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error('未连接'))
        return
      }
      const id = this.nextId++
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }

  // content 为纯文本字符串，或多模态 content blocks 列表（text + image 块）
  sendMessage(content: string | unknown[]): Promise<unknown> {
    return this.request('send_message', { content })
  }

  resume(value: unknown): Promise<unknown> {
    return this.request('resume', { value })
  }

  stop(): Promise<unknown> {
    return this.request('stop')
  }

  listCommands(): Promise<{ commands: SlashCommand[] }> {
    return this.request('list_commands') as Promise<{ commands: SlashCommand[] }>
  }

  runCommand(name: string, extraText: string): Promise<unknown> {
    return this.request('run_command', { name, extra_text: extraText })
  }

  listProviders(): Promise<{ profiles: ProviderProfile[]; active: ActiveModel }> {
    return this.request('list_providers') as Promise<{
      profiles: ProviderProfile[]
      active: ActiveModel
    }>
  }

  testProvider(
    baseUrl: string,
    apiKey: string,
    model: string,
  ): Promise<{ ok: boolean; error?: string; latency_ms?: number }> {
    return this.request('test_provider', {
      base_url: baseUrl,
      api_key: apiKey,
      model,
    }) as Promise<{ ok: boolean; error?: string; latency_ms?: number }>
  }

  setProvider(provider: string, model: string): Promise<{ active: ActiveModel; model: string }> {
    return this.request('set_provider', { provider, model }) as Promise<{
      active: ActiveModel
      model: string
    }>
  }

  saveProvider(
    profile: Partial<ProviderProfile>,
  ): Promise<{ profiles: ProviderProfile[]; active: ActiveModel }> {
    return this.request('save_provider', { profile }) as Promise<{
      profiles: ProviderProfile[]
      active: ActiveModel
    }>
  }

  deleteProvider(id: string): Promise<{ profiles: ProviderProfile[]; active: ActiveModel }> {
    return this.request('delete_provider', { id }) as Promise<{
      profiles: ProviderProfile[]
      active: ActiveModel
    }>
  }

  listSessions(): Promise<{ sessions: SessionMeta[] }> {
    return this.request('list_sessions', { limit: 50 }) as Promise<{ sessions: SessionMeta[] }>
  }

  newSession(): Promise<{ thread_id: string }> {
    return this.request('new_session') as Promise<{ thread_id: string }>
  }

  switchSession(threadId: string): Promise<{ thread_id: string }> {
    return this.request('switch_session', { thread_id: threadId }) as Promise<{
      thread_id: string
    }>
  }

  loadHistory(threadId: string): Promise<{ items: HistoryItem[] }> {
    return this.request('load_history', { thread_id: threadId }) as Promise<{
      items: HistoryItem[]
    }>
  }

  pinSession(threadId: string, pinned: boolean): Promise<unknown> {
    return this.request('pin_session', { thread_id: threadId, pinned })
  }

  renameSession(threadId: string, title: string): Promise<unknown> {
    return this.request('rename_session', { thread_id: threadId, title })
  }

  deleteSession(threadId: string): Promise<unknown> {
    return this.request('delete_session', { thread_id: threadId })
  }

  listCronJobs(): Promise<{ jobs: CronJob[] }> {
    return this.request('list_cron_jobs') as Promise<{ jobs: CronJob[] }>
  }

  createCronJob(name: string, schedule: string, prompt: string): Promise<{ job: CronJob }> {
    return this.request('create_cron_job', { name, schedule, prompt }) as Promise<{
      job: CronJob
    }>
  }

  updateCronJob(
    jobId: string,
    fields: { name?: string; schedule?: string; prompt?: string },
  ): Promise<{ job: CronJob }> {
    return this.request('update_cron_job', { job_id: jobId, ...fields }) as Promise<{
      job: CronJob
    }>
  }

  deleteCronJob(jobId: string): Promise<{ job_id: string }> {
    return this.request('delete_cron_job', { job_id: jobId }) as Promise<{ job_id: string }>
  }

  toggleCronJob(jobId: string, enabled: boolean): Promise<{ job: CronJob }> {
    return this.request('toggle_cron_job', { job_id: jobId, enabled }) as Promise<{
      job: CronJob
    }>
  }

  runCronJob(jobId: string): Promise<{ ok: boolean }> {
    return this.request('run_cron_job', { job_id: jobId }) as Promise<{ ok: boolean }>
  }

  listCronRuns(jobId: string, limit = 20): Promise<{ runs: CronRun[] }> {
    return this.request('list_cron_runs', { job_id: jobId, limit }) as Promise<{
      runs: CronRun[]
    }>
  }

  onEvent(h: EventHandler): () => void {
    this.eventHandlers.add(h)
    return () => this.eventHandlers.delete(h)
  }

  onState(h: StateHandler): () => void {
    this.stateHandlers.add(h)
    return () => this.stateHandlers.delete(h)
  }

  private setState(s: ConnState): void {
    for (const h of this.stateHandlers) h(s)
  }

  private flushPending(err: unknown): void {
    for (const p of this.pending.values()) p.reject(err)
    this.pending.clear()
  }

  close(): void {
    this.closedByUser = true
    this.ws?.close()
  }
}

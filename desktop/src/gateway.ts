// WS JSON-RPC 客户端：对接 lumi serve 的 /ws。
// 帧协议见 lumi/server/ws.py。带指数退避自动重连（sidecar 启动需要时间）。
import type {
  ActiveModel,
  BgTask,
  CronJob,
  CronRun,
  HistoryItem,
  Project,
  ProviderProfile,
  RpcMethod,
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
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  constructor(private readonly url: string) {}

  connect(): void {
    this.setState('connecting')
    const ws = new WebSocket(this.url)
    this.ws = ws
    ws.onopen = () => {
      this.retry = 0
      this.setState('open')
    }
    ws.onclose = (ev) => {
      this.setState('closed')
      // 连接断开：在飞的 RPC 不会再有响应，全部 reject 避免调用方永久挂起。
      // 关键如 send_message——否则其 Promise 永不 settle，且新连接不补发 turn.complete，
      // 会话会卡在 running 态、输入框永久禁用。
      this.flushPending(new Error('连接已断开'))
      // 1008 = 服务端鉴权拒绝（token 无效）：重连也会再被拒，别陷入无限 accept→1008→重连，
      // 停在 closed 让用户从机器连接灯看出是配置问题。
      if (ev.code === 1008) {
        this.closedByUser = true
        console.warn('[gateway] 鉴权失败 (1008)，停止重连：', this.url)
        return
      }
      if (!this.closedByUser) {
        const delay = Math.min(8000, 500 * 2 ** Math.min(this.retry++, 4))
        this.reconnectTimer = setTimeout(() => this.connect(), delay)
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

  request<T = unknown>(method: RpcMethod, params: Record<string, unknown> = {}): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error('未连接'))
        return
      }
      const id = this.nextId++
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }

  // content 为纯文本字符串，或多模态 content blocks 列表（text + image 块）
  sendMessage(content: string | unknown[]): Promise<unknown> {
    return this.request<{ commands: SlashCommand[] }>('send_message', { content })
  }

  resume(value: unknown): Promise<unknown> {
    return this.request('resume', { value })
  }

  stop(): Promise<unknown> {
    return this.request('stop')
  }

  listCommands(): Promise<{ commands: SlashCommand[] }> {
    return this.request('list_commands')
  }

  runCommand(name: string, extraText: string): Promise<unknown> {
    return this.request<{
      profiles: ProviderProfile[]
      active: ActiveModel
    }>('run_command', { name, extra_text: extraText })
  }

  listProviders(): Promise<{ profiles: ProviderProfile[]; active: ActiveModel }> {
    return this.request('list_providers')
  }

  setEffort(provider: string, model: string, level: string): Promise<{ effort: string }> {
    return this.request<{ effort: string }>('set_effort', { provider, model, level })
  }

  testProvider(
    baseUrl: string,
    apiKey: string,
    model: string,
  ): Promise<{ ok: boolean; error?: string; latency_ms?: number }> {
    return this.request<{ ok: boolean; error?: string; latency_ms?: number }>('test_provider', {
      base_url: baseUrl,
      api_key: apiKey,
      model,
    })
  }

  setProvider(provider: string, model: string): Promise<{ active: ActiveModel; model: string }> {
    return this.request<{
      active: ActiveModel
      model: string
    }>('set_provider', { provider, model })
  }

  saveProvider(
    profile: Partial<ProviderProfile>,
  ): Promise<{ profiles: ProviderProfile[]; active: ActiveModel }> {
    return this.request<{
      profiles: ProviderProfile[]
      active: ActiveModel
    }>('save_provider', { profile })
  }

  deleteProvider(id: string): Promise<{ profiles: ProviderProfile[]; active: ActiveModel }> {
    return this.request<{
      profiles: ProviderProfile[]
      active: ActiveModel
    }>('delete_provider', { id })
  }

  setWorkspace(path: string): Promise<{ workspace: string }> {
    return this.request<{ workspace: string }>('set_workspace', { path })
  }

  listProjects(): Promise<{ projects: Project[]; current: string }> {
    return this.request<{ projects: Project[]; current: string }>('list_projects')
  }

  addProject(path: string, name = ''): Promise<{ projects: Project[] }> {
    return this.request<{ projects: Project[] }>('add_project', { path, name })
  }

  removeProject(path: string): Promise<{ projects: Project[] }> {
    return this.request<{ projects: Project[] }>('remove_project', { path })
  }

  renameProject(path: string, name: string): Promise<{ projects: Project[] }> {
    return this.request<{ projects: Project[] }>('rename_project', { path, name })
  }

  // 远程目录浏览器：在该连接所属机器上浏览/建目录
  listDir(path = ''): Promise<{ path: string; parent: string | null; dirs: string[] }> {
    return this.request<{ path: string; parent: string | null; dirs: string[] }>('list_dir', { path })
  }

  makeDir(path: string): Promise<{ ok: boolean; path?: string; error?: string }> {
    return this.request<{ ok: boolean; path?: string; error?: string }>('make_dir', { path })
  }

  addFolder(path: string): Promise<{ folders: string[] }> {
    return this.request<{ folders: string[] }>('add_folder', { path })
  }

  removeFolder(path: string): Promise<{ folders: string[] }> {
    return this.request<{ folders: string[] }>('remove_folder', { path })
  }

  listSessions(): Promise<{ sessions: SessionMeta[] }> {
    return this.request<{ sessions: SessionMeta[] }>('list_sessions', { limit: 50 })
  }

  // workspace：会话所属项目目录；切入时让后端把进程 cwd 切过去（方案甲跨项目）
  switchSession(threadId: string, workspace = ''): Promise<{ thread_id: string }> {
    return this.request<{
      thread_id: string
    }>('switch_session', { thread_id: threadId, workspace })
  }

  loadHistory(threadId: string): Promise<{ items: HistoryItem[] }> {
    return this.request<{
      items: HistoryItem[]
    }>('load_history', { thread_id: threadId })
  }

  pinSession(threadId: string, pinned: boolean): Promise<unknown> {
    return this.request<{ jobs: CronJob[] }>('pin_session', { thread_id: threadId, pinned })
  }

  renameSession(threadId: string, title: string): Promise<unknown> {
    return this.request('rename_session', { thread_id: threadId, title })
  }

  deleteSession(threadId: string): Promise<unknown> {
    return this.request('delete_session', { thread_id: threadId })
  }

  listCronJobs(): Promise<{ jobs: CronJob[] }> {
    return this.request('list_cron_jobs')
  }

  createCronJob(name: string, schedule: string, prompt: string): Promise<{ job: CronJob }> {
    return this.request<{
      job: CronJob
    }>('create_cron_job', { name, schedule, prompt })
  }

  updateCronJob(
    jobId: string,
    fields: { name?: string; schedule?: string; prompt?: string },
  ): Promise<{ job: CronJob }> {
    return this.request<{
      job: CronJob
    }>('update_cron_job', { job_id: jobId, ...fields })
  }

  deleteCronJob(jobId: string): Promise<{ job_id: string }> {
    return this.request<{ job_id: string }>('delete_cron_job', { job_id: jobId })
  }

  toggleCronJob(jobId: string, enabled: boolean): Promise<{ job: CronJob }> {
    return this.request<{
      job: CronJob
    }>('toggle_cron_job', { job_id: jobId, enabled })
  }

  runCronJob(jobId: string): Promise<{ ok: boolean }> {
    return this.request<{ ok: boolean }>('run_cron_job', { job_id: jobId })
  }

  listCronRuns(jobId: string, limit = 20): Promise<{ runs: CronRun[] }> {
    return this.request<{
      runs: CronRun[]
    }>('list_cron_runs', { job_id: jobId, limit })
  }

  listBgTasks(): Promise<{ tasks: BgTask[] }> {
    return this.request<{ tasks: BgTask[] }>('list_bg_tasks')
  }

  stopBgTask(taskId: string): Promise<{ stopped: boolean; error?: string }> {
    return this.request<{ stopped: boolean; error?: string }>('stop_bg_task', {
      task_id: taskId,
    })
  }

  dismissBgTask(taskId: string): Promise<{ dismissed: boolean }> {
    return this.request<{ dismissed: boolean }>('dismiss_bg_task', { task_id: taskId })
  }

  clearFinishedBgTasks(): Promise<{ cleared: number }> {
    return this.request<{ cleared: number }>('clear_finished_bg_tasks')
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

  // 关闭后不可复用：退避中的重连定时器一并取消，否则定时器触发会复活
  // 一条无人引用的僵尸连接（服务端凭空多一个 bridge，且永久自动重连）
  close(): void {
    this.closedByUser = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
  }
}

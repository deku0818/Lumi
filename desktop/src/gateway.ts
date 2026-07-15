// WS JSON-RPC 客户端：对接 lumi serve 的 /ws。
// 帧协议见 lumi/server/ws.py。带指数退避自动重连（sidecar 启动需要时间）。
import type {
  ActiveModel,
  BgTask,
  ChannelInfo,
  ModelPointer,
  CronJob,
  CronRun,
  FeishuConfig,
  HistoryItem,
  McpScope,
  McpServerConfig,
  McpServers,
  McpServerStatus,
  McpTestResult,
  Project,
  ProviderProfile,
  RpcMethod,
  SessionMeta,
  SlashCommand,
  Usage,
  WireEvent,
} from './types'

// failed = 退避重试耗尽，已放弃自动重连，等用户主动点击重连
export type ConnState = 'connecting' | 'open' | 'closed' | 'failed'

const MAX_RETRY = 5 // 连续失败这么多次后停止自动重连

// 附带工具审批模式：toolMode 省略或 'default' 时不传 tool_mode（后端按默认处理）
function withToolMode<T extends object>(params: T, toolMode?: string): T {
  return toolMode && toolMode !== 'default' ? { ...params, tool_mode: toolMode } : params
}

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
  private currentState: ConnState = 'connecting'

  constructor(private url: string) {}

  // 地址可能在设置里被改（编辑远程机器的 url/token）；调用方在重连前更新，下次 connect 生效
  setUrl(url: string): void {
    this.url = url
  }

  // 绑定本连接的会话 thread：写进 URL，使断线重连自动携带 ?thread=，触发后端「断连续接」
  // ——接回断开期仍挂着的会话（parked 审批/运行轮原样还在），而非新建 bridge 丢掉它。
  // 握手拿到 thread 后调用一次即可。
  bindThread(threadId: string): void {
    if (!threadId) return
    try {
      const u = new URL(this.url)
      u.searchParams.set('thread', threadId)
      this.url = u.toString()
    } catch {
      /* 非法 URL：忽略，退回无续接（旧行为） */
    }
  }

  // 弃用当前 socket：解绑回调（否则其 onclose 还会再排一次重连）、reject 在飞请求、关闭。
  // reconnect() 可能在 open/connecting 态调用，不先弃用旧 socket 会泄漏它并引发重连风暴。
  private teardown(): void {
    // 清掉待定的退避重连计时器：否则 connect() 后它仍会触发，弃用刚建好的
    // socket 并另开一条，造成 socket churn（服务端多出一个 bridge）
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    const ws = this.ws
    if (!ws) return
    ws.onopen = ws.onclose = ws.onmessage = ws.onerror = null
    this.ws = null
    this.flushPending(new Error('连接已断开'))
    try {
      ws.close()
    } catch {
      /* 已关闭/未建立：忽略 */
    }
  }

  connect(): void {
    this.teardown()
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
      if (this.closedByUser) return
      // 退避重试耗尽：停在 failed 态，不再自动重连，等用户从连接灯主动点重连
      if (this.retry >= MAX_RETRY) {
        this.setState('failed')
        console.warn(`[gateway] 重连 ${MAX_RETRY} 次失败，停止自动重连：`, this.url)
        return
      }
      const delay = Math.min(8000, 500 * 2 ** Math.min(this.retry++, 4))
      this.reconnectTimer = setTimeout(() => this.connect(), delay)
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

  // content 为纯文本字符串，或多模态 content blocks 列表（text + image 块）；
  // files 为文件附件路径数组——后端统一拼标签给模型 + 写显示声明，前端不拼标签
  sendMessage(
    content: string | unknown[],
    toolMode?: string,
    files?: string[],
  ): Promise<unknown> {
    const params: Record<string, unknown> = { content }
    if (files && files.length > 0) params.files = files
    return this.request<{ commands: SlashCommand[] }>(
      'send_message',
      withToolMode(params, toolMode),
    )
  }

  // 在途审批应答（非流式控制 RPC）：approval_id 来自审批/clarify 事件 payload，
  // value 形状 = 审批 {decision,...} 或 clarify 答案/__ask_cancelled__。
  resume(approvalId: string, value: unknown): Promise<unknown> {
    return this.request('resume', { approval_id: approvalId, value })
  }

  stop(): Promise<unknown> {
    return this.request('stop')
  }

  listCommands(): Promise<{ commands: SlashCommand[] }> {
    return this.request('list_commands')
  }

  runCommand(name: string, extraText: string, toolMode?: string): Promise<unknown> {
    return this.request<{
      profiles: ProviderProfile[]
      active: ActiveModel
    }>('run_command', withToolMode({ name, extra_text: extraText }, toolMode))
  }

  listProviders(): Promise<{
    profiles: ProviderProfile[]
    active: ActiveModel
    classifier: ModelPointer
    titler: ModelPointer
  }> {
    return this.request('list_providers')
  }

  setEffort(provider: string, model: string, level: string): Promise<{ effort: string }> {
    return this.request<{ effort: string }>('set_effort', { provider, model, level })
  }

  // 设置/清除 auto 审批分类器模型（provider/model 均空 = 跟随会话模型）
  setClassifier(provider: string, model: string): Promise<{ classifier: ModelPointer }> {
    return this.request<{ classifier: ModelPointer }>('set_classifier', { provider, model })
  }

  // 设置/清除会话标题生成模型（provider/model 均空 = 跟随会话模型）
  setTitler(provider: string, model: string): Promise<{ titler: ModelPointer }> {
    return this.request<{ titler: ModelPointer }>('set_titler', { provider, model })
  }

  // 运行中实时切换工具审批模式：改后端共享 context，对当前轮后续工具立即生效
  setToolMode(toolMode: string): Promise<{ tool_mode: string }> {
    return this.request<{ tool_mode: string }>('set_tool_mode', { tool_mode: toolMode })
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

  // —— IM 渠道（飞书等）：配置存后端 ~/.lumi/channels.json，保存即实时重连 ——
  getChannels(): Promise<{ channels: ChannelInfo[] }> {
    return this.request<{ channels: ChannelInfo[] }>('get_channels')
  }

  saveChannel(name: string, config: Partial<FeishuConfig>): Promise<{ channels: ChannelInfo[] }> {
    return this.request<{ channels: ChannelInfo[] }>('save_channel', { name, config })
  }

  testChannel(
    name: string,
    config: Partial<FeishuConfig>,
  ): Promise<{ ok: boolean; error?: string; bot_name?: string }> {
    return this.request<{ ok: boolean; error?: string; bot_name?: string }>('test_channel', {
      name,
      config,
    })
  }

  // —— MCP 服务器：读写该机器的 ~/.lumi 或 <project>/.lumi 下 mcp_server.json，下次新会话加载生效 ——
  listMcpServers(scope: McpScope, project = ''): Promise<{ servers: McpServers }> {
    return this.request<{ servers: McpServers }>('list_mcp_servers', { scope, project })
  }

  // 项目会话池的最近加载状态（面板徽标）：project 空 = 全局池
  getMcpStatus(project = ''): Promise<{ loading: boolean; servers: McpServerStatus[] }> {
    return this.request<{ loading: boolean; servers: McpServerStatus[] }>('get_mcp_status', { project })
  }

  saveMcpServer(
    scope: McpScope,
    project: string,
    name: string,
    config: McpServerConfig,
  ): Promise<{ servers: McpServers }> {
    return this.request<{ servers: McpServers }>('save_mcp_server', { scope, project, name, config })
  }

  deleteMcpServer(scope: McpScope, project: string, name: string): Promise<{ servers: McpServers }> {
    return this.request<{ servers: McpServers }>('delete_mcp_server', { scope, project, name })
  }

  // 用给定配置临时连一次验证连通性并枚举工具/提示/资源，不动常驻会话池
  testMcpServer(config: McpServerConfig): Promise<McpTestResult> {
    return this.request<McpTestResult>('test_mcp_server', { config })
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

  setDefaultProject(path: string, isDefault: boolean): Promise<{ projects: Project[] }> {
    return this.request<{ projects: Project[] }>('set_default_project', { path, default: isDefault })
  }

  // 远程目录浏览器：在该连接所属机器上浏览/建目录
  listDir(path = ''): Promise<{
    path: string
    parent: string | null
    dirs: string[]
    selectable?: boolean
  }> {
    return this.request('list_dir', { path })
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

  // workspace：会话所属项目目录；切入时把本连接引擎绑定到该项目（会话级，不动进程 cwd）。
  // 新连接已在 open 握手 pin，这里多为切 thread；workspace 一致则后端跳过 rebase。
  switchSession(threadId: string, workspace = ''): Promise<{ thread_id: string }> {
    return this.request<{
      thread_id: string
    }>('switch_session', { thread_id: threadId, workspace })
  }

  loadHistory(threadId: string): Promise<{ items: HistoryItem[]; usage?: Usage }> {
    return this.request<{
      items: HistoryItem[]
      usage?: Usage
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
    this.currentState = s
    for (const h of this.stateHandlers) h(s)
  }

  get state(): ConnState {
    return this.currentState
  }

  // 是否已停止自我维持（鉴权拒绝/退避耗尽或被主动关闭）：此态下不会自行重连，
  // 调用方（如 openControlConn 幂等守卫）应据此判断要不要复活，而非仅看连接是否存在。
  get dead(): boolean {
    return this.closedByUser || this.currentState === 'failed'
  }

  private flushPending(err: unknown): void {
    for (const p of this.pending.values()) p.reject(err)
    this.pending.clear()
  }

  // 用户主动重连：清零退避计数，从 failed/closed 态重新发起连接
  reconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.retry = 0
    this.closedByUser = false
    this.connect()
  }

  // 关闭后不可复用：退避中的重连定时器一并取消，否则定时器触发会复活
  // 一条无人引用的僵尸连接（服务端凭空多一个 bridge，且永久自动重连）
  close(): void {
    this.closedByUser = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
  }
}

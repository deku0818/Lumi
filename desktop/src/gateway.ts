// WS JSON-RPC 客户端：对接 lumi serve 的 /ws。
// 帧协议见 lumi/server/ws.py。带指数退避自动重连（sidecar 启动需要时间）。
import type {
  ActiveModel,
  BgTask,
  BgTaskOutput,
  CatalogEntry,
  ChannelInfo,
  ModelLimits,
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
  DiagnoseCheck,
  EnvInstallTarget,
  EnvStatus,
  Project,
  ProjectOverview,
  ProjectResource,
  ProjectResourceKind,
  ProviderProfile,
  RpcMethod,
  SessionMeta,
  SessionModelWire,
  SlashCommand,
  TodoItem,
  Usage,
  WireEvent,
} from './types'
import { fmtSize } from '@/lib/utils'

// failed = 退避重试耗尽，已放弃自动重连，等用户主动点击重连
export type ConnState = 'connecting' | 'open' | 'closed' | 'failed'

// 连不上时的原因，供「连接」列表把机器行的副标题换成人话（'' = 没出过错）。
// 只分两类：服务端明确拒绝（1008 令牌无效）与其余一切连不通，多分也给不出不同的下一步
export type ConnError = '' | 'auth' | 'unreachable'

const MAX_RETRY = 5 // 连续失败这么多次后停止自动重连

// 附件上传上限，与后端 /upload 的 _MAX_FILE_BYTES 一致（ws.py）
const MAX_UPLOAD_BYTES = 128 * 1024 * 1024

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
  private lastError: ConnError = ''

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
      this.lastError = ''
      this.setState('open')
    }
    ws.onclose = (ev) => {
      // 原因先于状态记录：状态变更会同步通知订阅者，它们读的就是这里的值
      this.lastError = ev.code === 1008 ? 'auth' : 'unreachable'
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
  // files 为文件附件路径数组——后端统一拼标签给模型 + 写显示声明，前端不拼标签。
  // 本轮用户消息 id 由开轮的 turn.start 事件下发，不在 result 里
  sendMessage(
    content: string | unknown[],
    toolMode?: string,
    files?: string[],
  ): Promise<unknown> {
    const params: Record<string, unknown> = { content }
    if (files && files.length > 0) params.files = files
    return this.request('send_message', withToolMode(params, toolMode))
  }

  // 时间旅行重答：截断该用户消息及其后全部历史并重建重走一轮，事件流同 send_message
  regenerate(messageId: string, toolMode?: string): Promise<unknown> {
    return this.request('regenerate', withToolMode({ message_id: messageId }, toolMode))
  }

  // 编辑重发（原子）：截断该用户消息及其后全部历史，以编辑后文本 + 原附件重走一轮
  editResend(messageId: string, content: string, toolMode?: string): Promise<unknown> {
    return this.request(
      'edit_resend',
      withToolMode({ message_id: messageId, content }, toolMode),
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
    return this.request('run_command', withToolMode({ name, extra_text: extraText }, toolMode))
  }

  listProviders(): Promise<{
    profiles: ProviderProfile[]
    active: ActiveModel
    classifier: ModelPointer
    titler: ModelPointer
    fallback: ModelLimits
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

  // 按子串搜 models.dev 目录：手动指定「这个代理别名对应哪个模型」时用。空 query 返回空表。
  searchCatalog(query: string): Promise<{ entries: CatalogEntry[] }> {
    return this.request<{ entries: CatalogEntry[] }>('search_catalog', { query })
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

  /** 设「新会话默认模型」：不动任何已有会话（含本连接当前这个）。 */
  setProvider(provider: string, model: string): Promise<{ active: ActiveModel }> {
    return this.request<{ active: ActiveModel }>('set_provider', { provider, model })
  }

  /** 切换**本会话**的模型：按 thread 持久化，下一轮生效，不影响其他会话。 */
  setSessionModel(provider: string, model: string): Promise<SessionModelWire> {
    return this.request<SessionModelWire>('set_session_model', { provider, model })
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

  // —— IM 渠道（飞书等）：配置存后端 lumi.json（config_path 为其绝对路径，面板
  // 原样展示），保存即实时重连 ——
  getChannels(): Promise<{ channels: ChannelInfo[]; config_path: string }> {
    return this.request<{ channels: ChannelInfo[]; config_path: string }>('get_channels')
  }

  saveChannel(name: string, config: Partial<FeishuConfig>): Promise<{ channels: ChannelInfo[] }> {
    return this.request<{ channels: ChannelInfo[] }>('save_channel', { name, config })
  }

  // 删除一个飞书机器人（后端同时回收其 lark-cli 专属身份与会话池）
  deleteChannel(name: string, botId: string): Promise<{ channels: ChannelInfo[] }> {
    return this.request<{ channels: ChannelInfo[] }>('delete_channel', { name, bot_id: botId })
  }

  // 机器人接入体检（凭证 / 权限 / 事件 / 发布）：一次「应用版本信息」查询即可判全部四项
  diagnoseFeishuSetup(
    name: string,
    config: Partial<FeishuConfig>,
  ): Promise<{ checks: DiagnoseCheck[] }> {
    return this.request<{ checks: DiagnoseCheck[] }>('diagnose_feishu_setup', { name, config })
  }

  // 妙记链路体检（打开配置弹窗时调）：走子进程 + 网络，后端已丢线程池，可能耗时 1-2s
  diagnoseMinutes(name: string, config: Partial<FeishuConfig>): Promise<{ checks: DiagnoseCheck[] }> {
    return this.request<{ checks: DiagnoseCheck[] }>('diagnose_minutes', { name, config })
  }

  // —— 环境工具箱：agent 任务工具链（uv/rg/node + 飞书组件）的探测与安装 ——
  envStatus(): Promise<EnvStatus> {
    return this.request<EnvStatus>('env_status')
  }

  // 立即返回 started；进度走 env.progress 事件，结束广播 env.state 全量状态。
  // feishu-skills 需带 project（装到该项目 .lumi/skills/）
  envInstall(target: EnvInstallTarget = 'all', project = ''): Promise<{ started: boolean }> {
    return this.request<{ started: boolean }>('env_install', { target, project })
  }

  // docx/xlsx/pptx → 自包含 HTML（officecli 转换，按源文件 mtime 缓存）。
  // reason=missing 表示 officecli 未装，预览面板据此就地引导 envInstall('officecli')
  renderOffice(path: string): Promise<{ ok: boolean; html_path?: string; reason?: string; message?: string }> {
    return this.request('render_office', { path })
  }

  // HTTP 通道：从本连接的 WS 地址派生（ws→http 同主机同端口同 token）。
  // 原地改 URL 而非从 host 手拼：保留反代路径前缀（wss://x/lumi/ws → https://x/lumi/file）
  private httpUrl(endpoint: string, key: string, value: string): string {
    const u = new URL(this.url)
    u.protocol = u.protocol === 'wss:' ? 'https:' : 'http:'
    u.pathname = u.pathname.replace(/\/ws\/?$/, endpoint)
    const token = u.searchParams.get('token')
    u.search = ''
    u.searchParams.set(key, value)
    if (token) u.searchParams.set('token', token)
    return u.toString()
  }

  // 文件下行：远程后端的预览走它（lumi-file 只能读本机盘），office 渲染产物同理。
  fileHttpUrl(path: string): string {
    return this.httpUrl('/file', 'path', path)
  }

  // 附件上行的唯一决策点（对应下行的 contentUrl）：本地后端直接用路径（零拷贝），
  // 远程后端把内容传过去换成对端路径——前端本机的绝对路径在那台机器上不存在，
  // 直接发路径 agent 只会读到个 404。并发上传，慢链路上不按附件数累加等待。
  resolveAttachments(atts: { path: string; blob: File }[], remote: boolean): Promise<string[]> {
    if (!remote) return Promise.resolve(atts.map((a) => a.path))
    return Promise.all(atts.map((a) => this.uploadFile(a.blob)))
  }

  // 超限在本地先拦：服务端回 413 时请求体还没读完，连接一断浏览器只抛通用网络错，
  // 「文件太大」这个真正的原因反而永远到不了用户眼前。
  private async uploadFile(file: File): Promise<string> {
    if (file.size > MAX_UPLOAD_BYTES)
      throw new Error(`${file.name} 超过 ${fmtSize(MAX_UPLOAD_BYTES)} 上限`)
    const r = await fetch(this.httpUrl('/upload', 'name', file.name), {
      method: 'POST',
      body: file,
    })
    if (!r.ok) throw new Error(`${file.name} 上传失败（${r.status}）`)
    return ((await r.json()) as { path: string }).path
  }

  // —— MCP 服务器：读写该机器的全局层或 <project>/.lumi 下 mcp_server.json，下次新会话加载生效。
  // path = 该 scope 目标文件的绝对路径（面板原样展示）——
  listMcpServers(scope: McpScope, project = ''): Promise<{ servers: McpServers; path: string }> {
    return this.request<{ servers: McpServers; path: string }>('list_mcp_servers', {
      scope,
      project,
    })
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

  // ── 项目主页：按项目路径读写 prompts/skills/agents/memory（见 gateway/project_config.py）──
  projectOverview(path: string): Promise<ProjectOverview> {
    return this.request<ProjectOverview>('project_overview', { path })
  }

  projectResourceRead(
    path: string,
    kind: ProjectResourceKind,
    name: string,
    file = '',
  ): Promise<ProjectResource> {
    return this.request<ProjectResource>('project_resource_read', { path, kind, name, file })
  }

  projectResourceWrite(
    path: string,
    kind: ProjectResourceKind,
    name: string,
    content: string,
    file = '',
  ): Promise<{ ok: boolean; path: string }> {
    return this.request('project_resource_write', { path, kind, name, content, file })
  }

  projectResourceDelete(
    path: string,
    kind: ProjectResourceKind,
    name: string,
  ): Promise<{ ok: boolean; restored_builtin: boolean }> {
    return this.request('project_resource_delete', { path, kind, name })
  }

  projectCopyBuiltin(
    path: string,
    kind: ProjectResourceKind,
    name: string,
  ): Promise<{ ok: boolean; path: string }> {
    return this.request('project_copy_builtin', { path, kind, name })
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
  /** 切会话；结果带该会话生效的模型（模型是会话属性，不从全局 active 推导）。 */
  switchSession(
    threadId: string,
    workspace = '',
  ): Promise<{ thread_id: string } & SessionModelWire> {
    return this.request<{ thread_id: string } & SessionModelWire>('switch_session', {
      thread_id: threadId,
      workspace,
    })
  }

  loadHistory(threadId: string): Promise<{
    items: HistoryItem[]
    usage?: Usage
    model?: string
    context_window?: number
    todos?: TodoItem[]
  }> {
    return this.request<{
      items: HistoryItem[]
      usage?: Usage
      // 会话真实模型名与其上下文窗口：渠道旁观会话画上下文环的分母来源
      model?: string
      context_window?: number
      // 会话 state.todos 快照：右栏任务进度的历史还原
      todos?: TodoItem[]
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

  stopCronRun(jobId: string): Promise<{ stopped: boolean }> {
    return this.request<{ stopped: boolean }>('stop_cron_run', { job_id: jobId })
  }

  listCronRuns(jobId: string, limit = 20): Promise<{ runs: CronRun[] }> {
    return this.request<{
      runs: CronRun[]
    }>('list_cron_runs', { job_id: jobId, limit })
  }

  listBgTasks(): Promise<{ tasks: BgTask[] }> {
    return this.request<{ tasks: BgTask[] }>('list_bg_tasks')
  }

  readBgTaskOutput(taskId: string): Promise<BgTaskOutput> {
    return this.request<BgTaskOutput>('read_bg_task_output', { task_id: taskId })
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

  // 最近一次连接失败的原因；连上后清空
  get error(): ConnError {
    return this.lastError
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

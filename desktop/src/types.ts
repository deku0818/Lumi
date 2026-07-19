import events from '@protocol/events.json'

// 事件名/方法名从协议事实来源 protocol/events.json derive，无需手写同步。
// 不留 (string & {}) 逃生口——否则任何字符串都可赋值，tsc 对事件名拼写错误失明。
export type WireEventType = keyof typeof events.events
export type RpcMethod = keyof typeof events.methods

// LangChain usage_metadata 快照（事件 payload 里附带）。字段在不同 provider /
// 流式 vs 非流式补发下可能缺失，故全部可选；索引签名兜底未列出的 provider 私货字段。
export interface Usage {
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  input_token_details?: { cache_read?: number; [k: string]: number | undefined }
  [k: string]: unknown
}

// ask 工具的单个澄清问题（clarify.request）。后端形状宽松，至少含 question。
export interface Question {
  question: string
  [k: string]: unknown
}

// 审批请求里的单个工具调用摘要（approval.request）。
export interface ToolCallBrief {
  id?: string
  name?: string
  args?: unknown
  [k: string]: unknown
}

// 每个事件名 → payload 形状的单一映射。继承 Record<WireEventType, object> 兜底：
// 漏写任一事件名 tsc 即报错，保证覆盖全部事件。events.json 承载语言中立的同构契约。
export interface WireEventPayloads extends Record<WireEventType, object> {
  'gateway.ready': { model: string; workspace: string; workspace_bound: boolean; running?: boolean }
  'message.start': Record<string, never>
  'message.delta': { text: string; usage?: Usage }
  'thinking.delta': { text: string; usage?: Usage }
  'message.complete': { usage?: Usage }
  'tool.generating': Record<string, never>
  'compaction.status': { active: boolean }
  'tool.start': { name: string; args: unknown; tool_call_id: string; run_id?: string }
  'tool.complete': { name: string; output: string; tool_call_id: string; is_error?: boolean }
  'clarify.request': { approval_id: string; questions: Question[] }
  'approval.request': {
    approval_id: string
    tool_calls: ToolCallBrief[]
    decisions?: Record<string, unknown>
    options?: Record<string, unknown>
    warnings?: string[]
    boundary_violations?: string[]
  }
  'turn.complete': { usage?: Usage }
  error: { message: string }
  'cron.result': {
    job_id: string
    job_name: string
    status: string
    output: string
    started_at: string
    duration_ms: number
    thread_id: string // 空串=本次执行无可跳转会话（无 checkpointer / 已被清理）
  }
  'cron.running': { names: string[] }
  'bg_tasks.update': { tasks: BgTask[] }
  'channel.activity': { thread_id: string; channel: string }
  'session.title': { thread_id: string; title: string }
  'mcp.status': { project: string; servers: McpServerStatus[] }
}

// MCP 池加载结果（mcp.status 事件 / get_mcp_status RPC 共用形状）
export type McpServerStatus = { name: string; ok: boolean; tools?: number; error?: string }

// 判别联合：按 type 收窄到对应 payload。任意 payload 都可能附带 parent_run_id
// （非空=属于某子代理，见 events.json events_note）。
export type WireEvent = {
  [K in WireEventType]: {
    type: K
    session_id?: string
    payload: WireEventPayloads[K] & { parent_run_id?: string }
  }
}[WireEventType]

// 渲染项模型（前端聊天流的最小单元）
// 非图片附件：只引用绝对路径，气泡里渲染成文件胶囊
export interface AttachedFile {
  path: string
  name: string
}

export type Item =
  // sender：IM 渠道消息的发送者（desktop 消息无）；ts：所有用户消息的发送时刻（毫秒，
  // 底层统一落库）。气泡头渲染「李雷 · 14:02」或仅时间
  | { id: number; kind: 'user'; text: string; images?: string[]; files?: AttachedFile[]; sender?: string; ts?: number }
  | { id: number; kind: 'assistant'; text: string; streaming: boolean }
  | {
      id: number
      kind: 'tool'
      toolCallId: string
      name: string
      args: unknown
      output: string
      done: boolean
      error?: boolean
      // 子代理（name==='agent'）专用：自身 run_id（子工具事件经 parent_run_id 归属到此）、
      // 内部工具调用流、token 累计（来自子代理 message.complete 的 usage）
      runId?: string
      children?: SubTool[]
      inTok?: number
      outTok?: number
    }
  | { id: number; kind: 'notice'; text: string }

// 子代理内部的一次工具调用（不展开 output/diff，只展示调了什么）
export interface SubTool {
  toolCallId: string
  name: string
  args: unknown
  done: boolean
  error?: boolean
}

// 项目：手动登记的工作目录（~/.lumi/projects.json，按 last_used 降序下发）
export interface Project {
  name: string
  path: string
  last_used: number
  default?: boolean
}

// 斜杠命令（对齐后端 list_commands：当前为技能命令）
export interface SlashCommand {
  name: string
  description: string
  type: string
}

// 模型供应商 profile（对齐后端 provider_store.ProviderProfile）：一套连接挂多个模型
export interface ProviderProfile {
  id: string
  name: string
  base_url: string
  api_key: string
  models: string[]
  // 按模型的思考能力与当前档位（list_providers 附带，来自 models.dev）。
  // control 决定渲染形态：none 不渲染 / effort 档位列表 / toggle 开关。
  thinking?: Record<
    string,
    { control: 'none' | 'effort' | 'toggle'; levels: string[]; effort: string }
  >
  // 按模型的上下文窗口（tokens，来自 models.dev）。0 = 能力未知。
  context?: Record<string, number>
}

// 当前选中项：某 provider 下的某个 model
export interface ActiveModel {
  provider: string
  model: string
}

// 用途模型指针（providers 分区顶级 classifier / titler：auto 审批分类器、会话标题生成）。
// provider/model 均空 = 未配置（跟随会话模型）。
export type ModelPointer = { provider: string; model: string } | Record<string, never>

// 工具审批模式（对齐后端 LumiAgentState.tool_mode）
export type ToolMode = 'default' | 'accept_edits' | 'privileged' | 'auto'

// 会话元数据（对齐后端 list_sessions 的 SessionSummary 序列化）
export interface SessionMeta {
  thread_id: string
  first_message: string
  title: string
  pinned: boolean
  created_at: string
  message_count: number
  display_time: string
  workspace_dir: string // 所属项目目录；方案甲按机器→项目分组的 key
  channel?: string // IM 渠道会话标识（'feishu'），空/缺省 = desktop 会话
  channel_kind?: string // 渠道会话类型：'group' 群聊 / 'p2p' 私聊（图标区分）
  // 前端 fan-out 合并时打的机器标记（后端不发）；显示名由 backend + machines 现算
  backend?: string // 所属机器 id（'local' 或远程 id）
}

// 定时任务（对齐后端 cron_rpc._job_to_wire：Job.to_dict + next_run）
export interface CronJob {
  id: string
  name: string
  schedule: { type: 'at' | 'interval' | 'cron'; value: string }
  prompt: string
  enabled: boolean
  created_at: string
  consecutive_errors: number
  next_run: string | null
  // 前端 fan-out 合并时打的机器标记（后端不发）；显示名由 backend + machines 现算
  backend?: string
}

// 单次执行记录（对齐后端 RunRecord.to_dict）
export interface CronRun {
  job_id: string
  job_name: string
  started_at: string
  finished_at: string
  status: 'success' | 'failed' | 'timeout'
  duration_ms: number
  output_summary: string
  error: string
  // 本次执行的会话线程（cron- 前缀），非空时可跳转续聊；空串=无会话（旧记录或已清理）
  thread_id: string
}

// load_history 返回的历史项（对齐后端 _history_items）
export interface HistoryItem {
  kind: 'user' | 'assistant' | 'tool'
  text?: string
  sender?: string // IM 渠道消息发送者（additional_kwargs 结构化透传，非解析正文）
  ts?: number // 消息发送时刻（毫秒）
  images?: string[]
  files?: AttachedFile[]
  name?: string
  args?: unknown
  output?: string
  tool_call_id?: string
  done?: boolean
}

// 后台任务（bash / agent / workflow），后端 TaskRegistry 的序列化快照
export type BgTaskKind = 'bash' | 'agent' | 'workflow'
export type BgTaskStatus = 'running' | 'completed' | 'timed_out' | 'failed'

// workflow 实时聚合进度（B2 填充；B1 为 null）。形状由后端引擎定义，前端宽松消费。
export interface BgTaskProgress {
  phase?: string
  done?: number
  total?: number
  running?: number
  agent_count?: number
  [k: string]: unknown
}

export interface BgTask {
  task_id: string
  kind: BgTaskKind
  status: BgTaskStatus
  label: string
  agent_name: string | null
  thread_id: string
  started_at: number
  completed_at: number | null
  exit_code: number | null
  error: string | null
  agent_count: number | null
  output_file: string
  progress: BgTaskProgress | null
  // 前端收到 bg_tasks.update / list_bg_tasks 时打的机器标记（后端不发）：bg 任务是各机器
  // 进程级快照，同一飞书群 thread 在多台机器上会重名，靠 backend 区分归属。
  backend?: string
}

// present_files 工具返回的单个文件元数据（后端 providers/present_files.py）。
// kind ∈ image/pdf/video/audio/archive/doc/sheet/text/file；不存在的路径带 error。
export interface PresentedFile {
  path: string
  name?: string
  mime_type?: string
  size?: number
  kind?: string
  error?: string
}

// 远程后端（机器）注册项；本地 sidecar 是隐式后端（id='local'），不入此表。
// enabled=false：已配置但不连接（不开控制连接、侧栏隐藏）；缺省视为启用。
export type BackendRemote = { id: string; name: string; url: string; token: string; enabled?: boolean }
export type BackendsState = { active: string; remotes: BackendRemote[] }

// —— IM 渠道（飞书等）——
export type ChannelStatusState = 'off' | 'stopped' | 'connecting' | 'connected' | 'error'
export interface ChannelStatus {
  state: ChannelStatusState
  detail: string
}
export interface FeishuConfig {
  enabled: boolean
  app_id: string
  app_secret: string
  allow_from: string[]
  group_policy: 'mention' | 'open'
  // 运行时配置（对齐后端 ChannelRuntimeConfig，各渠道 config 继承）：模型 / 思考 / 审批 / 项目
  model: string // 空 = 跟随 desktop 全局 active 模型
  effort: string // 思考档位（依附 model，仅 model 非空时生效）；auto/low/high/xhigh/ultra…
  tool_mode: 'auto' | 'privileged'
  workspace: string
  minutes_enabled: boolean // 妙记纪要：录音/会议生成妙记后自动整理纪要推私聊
  daily_dream_enabled: boolean
  daily_dream_time: string
  summary_max_concurrency: number
}
// 妙记链路体检：四项前置条件（lark-cli / 授权 / 权限 / 订阅）逐项结果。
// 任一断裂的表现都是「静默收不到事件」，故必须逐项展示卡在哪一步。
export interface MinuteCheck {
  key: 'cli' | 'auth' | 'scope' | 'subscription'
  ok: boolean
  name: string
  detail: string
  fix_cmd: string
  fix_url: string
  fix_note: string
}
export interface ChannelInfo {
  name: string
  enabled: boolean
  config: FeishuConfig
  status: ChannelStatus
}

// —— MCP 服务器（设置 → MCP）——
export type McpScope = 'global' | 'project'
export type McpTransport = 'stdio' | 'streamable_http' | 'sse'
// 单个 server 的原始配置（含 Lumi 元字段 disabled）。加载侧剥离 disabled 后下传 adapter。
export interface McpServerConfig {
  transport?: McpTransport
  command?: string
  args?: string[]
  env?: Record<string, string>
  cwd?: string
  url?: string
  headers?: Record<string, string>
  timeout?: number
  sse_read_timeout?: number
  disabled?: boolean
}
// 一个 scope 下的全量 server 表：{ name: config }
export type McpServers = Record<string, McpServerConfig>

// —— MCP 连接测试（test_mcp_server）——
export interface McpToolInfo {
  name: string
  description: string
  input_schema?: Record<string, unknown> // 工具的 JSON Schema（properties/required）
}
export interface McpPromptInfo {
  name: string
  description: string
  arguments: { name: string; description: string; required: boolean }[]
}
export interface McpResourceInfo {
  uri: string
  name: string
  description: string
  mime_type: string
}
export interface McpTestResult {
  ok: boolean
  error?: string
  server?: { name: string; version: string }
  latency_ms?: number
  tools?: McpToolInfo[]
  prompts?: McpPromptInfo[]
  resources?: McpResourceInfo[]
}

declare global {
  interface Window {
    lumi: {
      platform?: string
      windowControls?: {
        minimize: () => Promise<void>
        toggleMaximize: () => Promise<boolean>
        close: () => Promise<void>
        isMaximized: () => Promise<boolean>
        onMaximizedChange: (cb: (maximized: boolean) => void) => () => void
      }
      menuCommand?: (command: string) => Promise<void>
      onMenuAction?: (cb: (action: string) => void) => () => void
      getConnection: (backendId?: string) => Promise<{ wsUrl: string }>
      backends?: {
        list: () => Promise<BackendsState>
        save: (b: Partial<BackendRemote>) => Promise<BackendsState>
        remove: (id: string) => Promise<BackendsState>
        setActive: (id: string) => Promise<{ active: string }>
      }
      pickDirectory?: () => Promise<string | null>
      getPathForFile?: (file: File) => string
      openPath?: (path: string) => Promise<string>
      revealInFolder?: (path: string) => Promise<void>
      pathExists?: (path: string) => Promise<boolean>
      notify?: (payload: { title: string; body?: string; tag?: string }) => Promise<void>
      onNotifyClick?: (cb: (tag: string) => void) => void
    }
  }
}

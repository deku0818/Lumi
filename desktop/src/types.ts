import events from '@protocol/events.json'

// 事件名/方法名从协议事实来源 protocol/events.json derive，无需手写同步。
// 不留 (string & {}) 逃生口——否则任何字符串都可赋值，tsc 对事件名拼写错误失明。
export type WireEventType = keyof typeof events.events
export type RpcMethod = keyof typeof events.methods

export interface WireEvent<P = any> {
  type: WireEventType
  session_id?: string
  payload: P
}

// 渲染项模型（前端聊天流的最小单元）
// 非图片附件：只引用绝对路径，气泡里渲染成文件胶囊
export interface AttachedFile {
  path: string
  name: string
}

export type Item =
  | { id: number; kind: 'user'; text: string; images?: string[]; files?: AttachedFile[] }
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

// 会话元数据（对齐后端 list_sessions 的 SessionSummary 序列化）
export interface SessionMeta {
  thread_id: string
  first_message: string
  title: string
  pinned: boolean
  created_at: string
  message_count: number
  display_time: string
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

declare global {
  interface Window {
    lumi: {
      getConnection: () => Promise<{ wsUrl: string }>
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

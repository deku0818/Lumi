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
export type Item =
  | { id: number; kind: 'user'; text: string; images?: string[] }
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
    }
  | { id: number; kind: 'notice'; text: string }

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
  name?: string
  args?: unknown
  output?: string
  tool_call_id?: string
  done?: boolean
}

declare global {
  interface Window {
    lumi: {
      getConnection: () => Promise<{ wsUrl: string }>
      pickDirectory?: () => Promise<string | null>
      notify?: (payload: { title: string; body?: string; tag?: string }) => Promise<void>
      onNotifyClick?: (cb: (tag: string) => void) => void
    }
  }
}

import events from '@protocol/events.json'

// 事件名/方法名从协议事实来源 protocol/events.json derive，无需手写同步。
export type WireEventType = keyof typeof events.events | (string & {})
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
      focusWindow?: () => Promise<void>
      notify?: (payload: { title: string; body?: string; tag?: string }) => Promise<void>
      onNotifyClick?: (cb: (tag: string) => void) => void
      log?: (msg: string) => void
    }
  }
}

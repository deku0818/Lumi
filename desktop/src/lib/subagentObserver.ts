import type { Gateway } from '../gateway.ts'
import type { SubagentEvent } from '../types.ts'

type Client = Pick<Gateway, 'state' | 'onEvent' | 'observeSubagent' | 'subagentHistory' | 'unobserveSubagent'>
export interface SubagentFeedUpdate {
  events?: SubagentEvent[]
  cursor?: number
  nativeId?: string | null
  loading?: boolean
  error?: string
}

/** The cursor advances only through persisted history, never through lossy live delivery. */
export function observeSubagent(client: Client, agentId: string, after: number, update: (value: SubagentFeedUpdate) => void) {
  let active = true, fetching = false, dirty = false, subscribe = true, cursor = after
  let taskState = ''
  async function refresh(resubscribe = false): Promise<void> {
    dirty = true
    subscribe ||= resubscribe
    if (fetching || !active || client.state !== 'open') return
    fetching = true
    while (active && dirty && client.state === 'open') {
      dirty = false
      update({ loading: true, error: '' })
      try {
        const subscribing = subscribe
        subscribe = false
        let page = await (subscribing ? client.observeSubagent(agentId, cursor) : client.subagentHistory(agentId, cursor))
        while (active) {
          const before = cursor
          cursor = page.cursor
          update({ events: page.events, cursor, nativeId: page.native_session_id })
          if (page.events.length < 500 || cursor <= before) break
          page = await client.subagentHistory(agentId, cursor)
        }
      } catch (error) {
        subscribe = true
        if (active) update({ error: String(error instanceof Error ? error.message : error) })
      }
      if (active) update({ loading: false })
    }
    fetching = false
  }
  const off = client.onEvent(event => {
    if (event.type === 'gateway.ready') void refresh(true)
    if (event.type === 'bg_tasks.update') {
      const next = event.payload.tasks.filter(task => task.subagent_id === agentId)
        .map(task => `${task.task_id}:${task.status}:${task.progress?.phase}`).sort().join('|')
      if (next !== taskState) { taskState = next; if (next) void refresh() }
    }
    if (event.type !== 'subagent.event' || event.payload.agent_id !== agentId) return
    const { agent_id, turn_id, seq, event_type, data } = event.payload
    update({ events: [{ agent_id, turn_id, seq, type: event_type, payload: data }] })
    if (event_type === 'turn.complete') void refresh()
  })
  void refresh()
  return {
    refresh: () => { void refresh() },
    close: () => {
      active = false
      off()
      if (client.state === 'open') void client.unobserveSubagent(agentId).catch(() => {})
    },
  }
}

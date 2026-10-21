import type { Item, Question, SubagentEvent, SubagentTurnStatus, ToolItem } from '../types.ts'

export interface SubagentRound {
  id: string
  ordinal: number
  prompt: string
  status: SubagentTurnStatus
  items: Item[]
  thinking: string
  streaming: boolean
  error: string
  questions: Question[]
  assistantIds: Record<string, string>
  parents: Record<string, string>
}
export interface SubagentTimeline {
  events: SubagentEvent[]
  rounds: SubagentRound[]
}
export const emptySubagentTimeline: SubagentTimeline = { events: [], rounds: [] }

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function displayValue(value: unknown): string {
  if (value == null) return ''
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}
function blocksText(message: unknown, type: string): string {
  const content = record(message).content
  if (!Array.isArray(content)) return typeof content === 'string' && type === 'text' ? content : ''
  return content.map(record).map(block => typeof block[type] === 'string' ? block[type] : '').join('')
}

function questionList(value: unknown): Question[] {
  if (!Array.isArray(value)) return []
  return value.map(record).map(question => ({
    question: String(question.question || ''),
    header: String(question.header || ''),
    multiSelect: !!question.multiSelect,
    options: (Array.isArray(question.options) ? question.options : []).map(record).map(option => ({
      label: String(option.label || ''), description: String(option.description || ''),
    })),
  }))
}

function applyEvent(rounds: SubagentRound[], event: SubagentEvent): SubagentRound[] {
  const index = rounds.findIndex(round => round.id === event.turn_id)
  const current = rounds[index] ?? {
    id: event.turn_id, ordinal: rounds.length + 1, prompt: '', status: 'queued',
    items: [], thinking: '', streaming: false, error: '', questions: [], assistantIds: {}, parents: {},
  }
  let round: SubagentRound = { ...current }
  const data = event.payload
  const parent = String(data.parent_tool_use_id || record(data.message).parent_tool_use_id || '')
  const key = data.run_id || data.message_id
  let kind = event.type
  let text = typeof data.text === 'string' ? data.text : ''
  // Older native records remain readable; adapters expose a common timeline to the view.
  const nativeEvent = record(data.event)
  const nativeItem = record(data.native_item)
  if (kind === 'native.event' && nativeEvent.type === 'message_start') {
    round.assistantIds = { ...round.assistantIds, [parent]: String(record(nativeEvent.message).id || event.seq) }
  } else if (kind === 'native.event' && ['item/reasoning/summaryTextDelta', 'item/reasoning/textDelta'].includes(String(data.method))) {
    kind = 'thinking.delta'
    text = String(data.delta || '')
  }

  if (!parent) {
    if (kind === 'thinking.delta') round.streaming = false
    else if (['message.start', 'message.delta', 'message.complete', 'tool.start', 'tool.complete', 'turn.complete'].includes(kind)
      || kind === 'native.event' && nativeEvent.type === 'message_start') {
      round.thinking = ''
      round.streaming = kind === 'message.delta'
    }
  }

  if (kind === 'message.submitted') {
    round.prompt = text
    round.ordinal = Number(data.ordinal) || round.ordinal
  } else if (kind === 'turn.start') {
    round.status = 'running'
  } else if (kind === 'message.start') {
    round.assistantIds = { ...round.assistantIds, [parent]: String(key || event.seq) }
  } else if (kind === 'message.delta' || kind === 'message.complete') {
    const complete = kind === 'message.complete'
    if (complete && !text) text = blocksText(data.message, 'text')
    const identity = String(key || round.assistantIds[parent] || event.seq)
    const id = `reply:${round.id}:${identity}`
    const found = round.items.find(item => item.id === id)
    const previous = found?.kind === 'assistant' ? found.text : ''
    const value = complete ? text || previous : previous + text
    if (value || found) {
      const item: Item = { id, kind: 'assistant', text: value, streaming: !complete }
      round.items = found ? round.items.map(old => old.id === id ? item : old) : [...round.items, item]
    }
    round.assistantIds = { ...round.assistantIds, [parent]: identity }
    if (parent) round.parents = { ...round.parents, [id]: parent }
  } else if (kind === 'thinking.delta') {
    if (!parent) round.thinking += text
  } else if (kind === 'tool.start' || kind === 'tool.complete') {
    const toolId = String(data.tool_call_id || data.run_id || event.seq)
    const found = round.items.find(item => item.kind === 'tool' && item.toolCallId === toolId) as ToolItem | undefined
    const output = data.output ?? nativeItem.aggregatedOutput ?? nativeItem.output ?? nativeItem.result
    const nativeName = String(data.name || found?.name || 'tool')
    const name = ({ commandExecution: 'bash', fileChange: 'edit', Bash: 'bash', Read: 'read', Write: 'write', Edit: 'edit', Glob: 'glob', Grep: 'grep' } as Record<string, string>)[nativeName] || nativeName
    const item: ToolItem = {
      id: found?.id || `tool:${round.id}:${toolId}`, kind: 'tool', toolCallId: toolId,
      name,
      args: data.args ?? found?.args ?? nativeItem.arguments ?? (nativeItem.command ? { command: nativeItem.command, cwd: nativeItem.cwd } : nativeItem),
      output: kind === 'tool.complete' ? displayValue(output ?? nativeItem) : found?.output || '',
      done: kind === 'tool.complete',
      error: !!data.is_error || nativeItem.status === 'failed' || (typeof nativeItem.exitCode === 'number' && nativeItem.exitCode !== 0),
    }
    round.items = found ? round.items.map(old => old.id === item.id ? item : old) : [...round.items, item]
    if (parent) round.parents = { ...round.parents, [String(item.id)]: parent }
  } else if (kind === 'turn.complete') {
    round.status = data.status as SubagentTurnStatus
    round.error = String(data.error || '')
    round.questions = questionList(data.questions)
    round.items = round.items.map(item => item.kind === 'assistant' ? { ...item, streaming: false }
      : item.kind === 'tool' && !item.done ? { ...item, done: true } : item)
    const output = String(data.output || '')
    const replies = round.items.flatMap(item => {
      if (item.kind !== 'assistant' || round.parents[String(item.id)]) return []
      return [item.text]
    }).join('\n')
    if (output && !replies.endsWith(output)) {
      round.items = [...round.items, { id: `result:${round.id}`, kind: 'assistant', text: output, streaming: false }]
    }
  }
  if (index < 0) return [...rounds, round]
  return rounds.map((old, i) => i === index ? round : old)
}

/** Append live events incrementally; rebuild only for genuinely missing older records. */
export function mergeSubagentTimeline(previous: SubagentTimeline, incoming: SubagentEvent[]): SubagentTimeline {
  const known = new Set(previous.events.map(event => event.seq))
  const fresh = incoming.filter(event => {
    if (known.has(event.seq)) return false
    known.add(event.seq)
    return true
  }).sort((a, b) => a.seq - b.seq)
  if (!fresh.length) return previous
  if (fresh[0].seq < (previous.events.at(-1)?.seq ?? 0)) {
    const events = [...previous.events, ...fresh].sort((a, b) => a.seq - b.seq)
    return { events, rounds: events.reduce(applyEvent, [] as SubagentRound[]) }
  }
  return { events: [...previous.events, ...fresh], rounds: fresh.reduce(applyEvent, previous.rounds) }
}

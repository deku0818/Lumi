import { useCallback, useEffect, useRef, useState } from 'react'
import type { Gateway } from '../gateway.ts'
import type { SubagentSummary, SubagentViewState } from '../types.ts'
import { emptySubagentTimeline, mergeSubagentTimeline } from '../lib/subagentTimeline.ts'
import { observeSubagent } from '../lib/subagentObserver.ts'
import { errorMessage } from '../lib/utils.ts'
import { useLatest } from './useLatest.ts'

const emptyDetail = { ...emptySubagentTimeline, cursor: 0, nativeId: null as string | null, loading: false, error: '' }

export function useSubagents(gateway: Gateway | undefined, owner: string, parentId: string, enabled: boolean, visible: boolean) {
  const [list, setList] = useState({ owner: '', agents: [] as SubagentSummary[], error: '' })
  const [selection, setSelection] = useState({ owner: '', id: null as string | null, expanded: false })
  const [feeds, setFeeds] = useState({ owner: '', entries: {} as Record<string, typeof emptyDetail> })
  const cached = useLatest(feeds)
  const observers = useRef(new Map<string, ReturnType<typeof observeSubagent>>())
  const refreshRef = useRef(() => {})
  const positions = useRef(new Map<string, SubagentViewState>())
  const selectedId = selection.owner === owner ? selection.id : null
  const expanded = !!selectedId && selection.expanded
  const agents = list.owner === owner ? list.agents : []
  const details = feeds.owner === owner ? feeds.entries : {}
  const positionKey = `${owner}\0${selectedId}`
  if (!positions.current.has(positionKey)) positions.current.set(positionKey, { top: 0, pinned: true })
  const position = positions.current.get(positionKey)!

  useEffect(() => {
    if (!gateway || !enabled || !parentId) return
    let alive = true, fetching = false, dirty = false, signature = ''
    async function refresh(): Promise<void> {
      dirty = true
      if (fetching || !alive || gateway!.state !== 'open') return
      fetching = true
      while (alive && dirty && gateway!.state === 'open') {
        dirty = false
        try {
          const result = await gateway!.listSubagents()
          if (alive) setList({ owner, agents: result.agents, error: '' })
        } catch (error) {
          if (alive) setList(old => ({ owner, agents: old.owner === owner ? old.agents : [], error: errorMessage(error) }))
        }
      }
      fetching = false
    }
    refreshRef.current = () => { void refresh() }
    const off = gateway.onEvent(event => {
      if (event.type === 'gateway.ready') void refresh()
      if (event.type === 'bg_tasks.update') {
        const next = event.payload.tasks.filter(task => task.thread_id === parentId && task.subagent_id)
          .map(task => `${task.task_id}:${task.status}:${task.progress?.phase}`).sort().join('|')
        if (next !== signature) { signature = next; void refresh() }
      }
      if (event.type === 'subagent.event' && event.payload.event_type === 'turn.complete') void refresh()
    })
    void refresh()
    return () => { alive = false; off(); refreshRef.current = () => {} }
  }, [gateway, owner, parentId, enabled])

  // One observer per visible instance: cards and detail consume the same timeline.
  useEffect(() => {
    const current = observers.current
    const refresh = () => current.forEach(observer => observer.refresh())
    window.addEventListener('focus', refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      current.forEach(observer => observer.close())
      current.clear()
    }
  }, [gateway, owner])

  useEffect(() => {
    const wanted = new Set<string>()
    if (gateway && enabled && (visible || expanded)) {
      if (selectedId) wanted.add(selectedId)
      else for (const agent of agents) if (agent.status === 'running') wanted.add(agent.agent_id)
    }
    for (const [id, observer] of observers.current) {
      if (!wanted.has(id)) { observer.close(); observers.current.delete(id) }
    }
    for (const id of wanted) {
      if (observers.current.has(id)) continue
      const previous = cached.current.owner === owner ? cached.current.entries[id] : undefined
      const observer = observeSubagent(gateway!, id, previous?.cursor ?? 0, change => {
        setFeeds(old => {
          const entries = old.owner === owner ? old.entries : {}
          const current = entries[id] ?? emptyDetail
          return { owner, entries: { ...entries, [id]: {
            ...current, ...mergeSubagentTimeline(current, change.events ?? []),
            cursor: change.cursor ?? current.cursor,
            nativeId: change.nativeId === undefined ? current.nativeId : change.nativeId,
            loading: change.loading ?? current.loading,
            error: change.error ?? current.error,
          } } }
        })
      })
      observers.current.set(id, observer)
    }
  }, [gateway, owner, enabled, visible, expanded, selectedId, agents])

  const select = useCallback((id: string) => setSelection(old => ({ owner, id, expanded: old.owner === owner && old.expanded })), [owner])
  const close = useCallback(() => setSelection({ owner, id: null, expanded: false }), [owner])
  const toggleExpanded = useCallback(() => setSelection(old => ({ ...old, expanded: !old.expanded })), [])
  return {
    agents, details, listError: list.owner === owner ? list.error : '',
    selectedId, expanded, select, close, toggleExpanded, position,
    detail: selectedId ? details[selectedId] ?? emptyDetail : emptyDetail,
    refresh: () => refreshRef.current(),
    retryDetail: () => { if (selectedId) observers.current.get(selectedId)?.refresh() },
  }
}

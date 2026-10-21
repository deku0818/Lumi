import { useCallback, useEffect, useRef, useState } from 'react'
import type { Gateway } from '../gateway.ts'
import type { SubagentSummary, SubagentViewState } from '../types.ts'
import { emptySubagentTimeline, mergeSubagentTimeline } from '../lib/subagentTimeline.ts'
import { observeSubagent } from '../lib/subagentObserver.ts'

const emptyDetail = { ...emptySubagentTimeline, owner: '', id: '', cursor: 0, nativeId: null as string | null, loading: false, error: '' }

export function useSubagents(gateway: Gateway | undefined, owner: string, parentId: string, enabled: boolean, visible: boolean) {
  const [list, setList] = useState({ owner: '', agents: [] as SubagentSummary[], error: '' })
  const [selection, setSelection] = useState({ owner: '', id: null as string | null, expanded: false })
  const [detail, setDetail] = useState(emptyDetail)
  const cached = useRef(detail)
  cached.current = detail
  const refreshRef = useRef(() => {})
  const detailRefresh = useRef(() => {})
  const positions = useRef(new Map<string, SubagentViewState>())
  const selectedId = selection.owner === owner ? selection.id : null
  const expanded = !!selectedId && selection.expanded
  const observing = enabled && (visible || expanded)
  const positionKey = `${owner}\0${selectedId}`
  if (!positions.current.has(positionKey)) positions.current.set(positionKey, { top: 0, pinned: true, folds: {} })
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
          if (alive) setList(old => ({ owner, agents: old.owner === owner ? old.agents : [], error: String(error instanceof Error ? error.message : error) }))
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

  useEffect(() => {
    if (!gateway || !observing || !selectedId) return
    const previous = cached.current
    const cursor = previous.owner === owner && previous.id === selectedId ? previous.cursor : 0
    setDetail(old => old.owner === owner && old.id === selectedId ? old : { ...emptyDetail, owner, id: selectedId, loading: true })
    const observer = observeSubagent(gateway, selectedId, cursor, change => {
      setDetail(old => {
        const current = old.owner === owner && old.id === selectedId ? old : { ...emptyDetail, owner, id: selectedId }
        return {
          ...current, ...mergeSubagentTimeline(current, change.events ?? []),
          cursor: change.cursor ?? current.cursor,
          nativeId: change.nativeId === undefined ? current.nativeId : change.nativeId,
          loading: change.loading ?? current.loading,
          error: change.error ?? current.error,
        }
      })
    })
    detailRefresh.current = observer.refresh
    window.addEventListener('focus', observer.refresh)
    return () => { window.removeEventListener('focus', observer.refresh); observer.close(); detailRefresh.current = () => {} }
  }, [gateway, owner, selectedId, observing])

  const select = useCallback((id: string) => setSelection(old => ({ owner, id, expanded: old.owner === owner && old.expanded })), [owner])
  const close = useCallback(() => setSelection({ owner, id: null, expanded: false }), [owner])
  const toggleExpanded = useCallback(() => setSelection(old => ({ ...old, expanded: !old.expanded })), [])
  return {
    agents: list.owner === owner ? list.agents : [], listError: list.owner === owner ? list.error : '',
    selectedId, expanded, select, close, toggleExpanded, position,
    detail: detail.owner === owner && detail.id === selectedId ? detail : emptyDetail,
    refresh: () => refreshRef.current(),
    retryDetail: () => detailRefresh.current(),
  }
}

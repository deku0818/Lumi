import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronDown, ExternalLink, Eye, Maximize2, Minimize2, Square, X } from 'lucide-react'
import type { Artifact, SubagentSummary, SubagentViewState } from '../types'
import type { SubagentRound, SubagentTimeline } from '../lib/subagentTimeline'
import { groupItems, segKey } from '../lib/chatSegments'
import { errorMessage } from '../lib/utils'
import { useI18n } from '../i18n'
import { RailSection } from './RightRail'
import { ChatContent, ChatSegment } from './chat/ChatContent'
import { ItemView, StatusIndicator } from './chat/ItemView'
import { CopyButton } from './chat/MessageActions'
import { Button } from './ui/button'
import { QuestionContent } from './QuestionContent'

function SubagentHeading({ agent }: { agent: SubagentSummary }) {
  return <span className="flex min-w-0 items-center gap-2" title={agent.cwd}>
    <span className="min-w-0 truncate text-[13px] font-semibold" title={agent.title || agent.name}>{agent.title || agent.name}</span>
    {agent.title && <span className="shrink-0 rounded-[5px] bg-surface px-1.5 text-[11.5px] font-normal text-muted-foreground">{agent.name}</span>}
  </span>
}

function SubagentActivity({ round }: { round?: SubagentRound }) {
  return <StatusIndicator variant="orbit" items={round?.items ?? []} running={!round || round.status === 'running'} waiting={false}
    streaming={round?.streaming ?? false} thinkingText={round?.thinking ?? ''} compacting={false} />
}

export function SubagentsSection({ agents, timelines = {}, selectedId, error, onSelect, onStop }: {
  agents: SubagentSummary[]; timelines?: Readonly<Record<string, SubagentTimeline>>; selectedId?: string | null
  error: string; onSelect: (id: string) => void; onStop: (agent: SubagentSummary) => Promise<void>
}) {
  const { t } = useI18n()
  const running = agents.filter(agent => agent.status === 'running').length
  const queued = agents.filter(agent => agent.status === 'queued').length
  const activeCount = [running ? t('subagent.runningCount', { n: running }) : '', queued ? t('subagent.queuedInstances', { n: queued }) : ''].filter(Boolean).join(' · ')
  const count = activeCount || (agents.length > 0 && agents.every(agent => agent.last_turn_status === 'completed') ? t('subagent.completedCount', { n: agents.length }) : agents.length)
  if (!agents.length && !error) return null
  return <RailSection title={t('subagent.title')} count={count} className="app-canvas">
    <div className="px-2 pb-2 space-y-2">
      {error && <p role="alert" className="px-2 text-xs text-error break-words">{error}</p>}
      {agents.map(agent => <AgentCard key={agent.agent_id} agent={agent} selected={agent.agent_id === selectedId}
        round={timelines[agent.agent_id]?.rounds.find(round => round.id === agent.active_turn_id)} onSelect={onSelect} onStop={onStop} />)}
    </div>
  </RailSection>
}

function AgentCard({ agent, round, selected, onSelect, onStop }: {
  agent: SubagentSummary; round?: SubagentRound; selected: boolean
  onSelect: (id: string) => void; onStop: (agent: SubagentSummary) => Promise<void>
}) {
  const { t } = useI18n()
  const status = agent.status === 'idle' ? agent.last_turn_status : agent.status
  const error = ['failed', 'blocked', 'interrupted'].includes(status)
  return <article className={`group relative cursor-pointer rounded-lg border ${selected ? 'border-primary/40' : 'border-line/70'}`} onClick={event => {
    if (!(event.target as HTMLElement).closest('button')) onSelect(agent.agent_id)
  }}>
    <button onClick={() => onSelect(agent.agent_id)} className="block w-full rounded-lg px-3 py-2.5 pr-8 text-left hover:bg-ink/[0.02]" aria-label={agent.title || agent.name}>
      <SubagentHeading agent={agent} />
    </button>
    {status === 'running' && <div className="px-3 pb-3"><SubagentActivity round={round} /></div>}
    {(status === 'queued' || status === 'running' && agent.queued_count > 0) && <p className="px-3 pb-2 text-xs text-muted-foreground">
      {agent.queued_count > 0 ? t('subagent.queuedCount', { n: agent.queued_count }) : t('subagent.status.queued')}
    </p>}
    {(error || status === 'cancelled') && <p role={error ? 'alert' : 'status'} className={`px-3 pb-2 text-xs ${error ? 'text-error' : 'text-muted-foreground'}`}>{t(`subagent.status.${status}`)}</p>}
    <SubagentStop agent={agent} onStop={onStop} compact />
  </article>
}

function SubagentStop({ agent, onStop, compact = false }: {
  agent: SubagentSummary; onStop: (agent: SubagentSummary) => Promise<void>; compact?: boolean
}) {
  const { t } = useI18n()
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState('')
  if (!agent.active_turn_id) return null
  return <>
    <Button variant="ghost" size={compact ? 'icon-sm' : 'sm'}
      className={compact ? 'absolute right-1 top-1 size-6 text-muted-foreground opacity-0 group-hover:opacity-100 focus-visible:opacity-100' : 'ml-auto text-xs'}
      disabled={stopping} title={t('subagent.stop')} aria-label={t('subagent.stop')} onClick={() => {
        setStopping(true); setError('')
        void onStop(agent).catch(reason => setError(errorMessage(reason))).finally(() => setStopping(false))
      }}><Square size={compact ? 11 : 13} />{!compact && t('composer.stop')}</Button>
    {error && <p role="alert" className="px-2 pb-2 text-xs text-error break-words">{error}</p>}
  </>
}

type DetailProps = {
  agent: SubagentSummary
  detail: SubagentTimeline & { nativeId: string | null; loading: boolean; error: string }
  expanded: boolean
  remote: boolean
  online: boolean
  onBack: () => void
  onExpand: () => void
  onRetry: () => void
  onOpenFile: (file: Artifact) => void
  onStop: (agent: SubagentSummary) => Promise<void>
  position: SubagentViewState
}

export function SubagentDetail({ agent, detail, expanded, remote, online, onBack, onExpand, onRetry, onOpenFile, onStop, position }: DetailProps) {
  const { t } = useI18n()
  const scroll = useRef<HTMLDivElement>(null)
  const restored = useRef<SubagentViewState | null>(null)
  const [jump, setJump] = useState(false)
  const [nativeError, setNativeError] = useState('')
  const nativeId = detail.nativeId
  useLayoutEffect(() => {
    const element = scroll.current
    if (!element) return
    if (restored.current !== position) {
      // Wait for all history pages before applying an offset to an empty/partial viewport.
      if (detail.loading || detail.error || !detail.rounds.length) return
      element.scrollTop = position.pinned ? element.scrollHeight : position.top
      restored.current = position
      setJump(!position.pinned)
    } else if (position.pinned) {
      element.scrollTop = element.scrollHeight
    }
  }, [position, detail.rounds, detail.loading, detail.error])
  return <section aria-label={agent.title || agent.name} className={`flex min-h-0 flex-1 flex-col overflow-hidden app-canvas ${expanded ? '' : 'sidebar-float rounded-card'}`}>
    <header className="titlebar-interactive flex shrink-0 items-center gap-1.5 border-b border-line/60 px-2 py-2">
      <Button variant="ghost" size={expanded ? 'sm' : 'icon-sm'} title={t(expanded ? 'subagent.backMain' : 'subagent.back')} aria-label={t(expanded ? 'subagent.backMain' : 'subagent.back')} onClick={onBack}><ArrowLeft />{expanded && <span className="text-xs">{t('subagent.backMain')}</span>}</Button>
      <div className="min-w-0 flex-1"><SubagentHeading agent={agent} /></div>
      {!remote && agent.executor === 'codex' && nativeId && window.lumi.openCodexThread && <Button variant="ghost" size="icon-sm" title={t('subagent.nativeOpen')} aria-label={t('subagent.nativeOpen')} onClick={() => { setNativeError(''); void window.lumi.openCodexThread!(nativeId).catch(error => setNativeError(errorMessage(error))) }}><ExternalLink size={14} /></Button>}
      {nativeId && agent.executor !== 'lumi' && <span title={t('subagent.nativeId')}><CopyButton text={nativeId} /></span>}
      <Button variant="ghost" size="icon-sm" title={t(expanded ? 'subagent.shrink' : 'subagent.expand')} aria-label={t(expanded ? 'subagent.shrink' : 'subagent.expand')} onClick={onExpand}>{expanded ? <Minimize2 /> : <Maximize2 />}</Button>
      {!expanded && <Button variant="ghost" size="icon-sm" title={t('common.close')} aria-label={t('common.close')} onClick={onBack}><X /></Button>}
    </header>
    {(!online || detail.error || nativeError) && <div role="alert" className="shrink-0 px-4 py-2 text-xs text-error break-words">{detail.error || nativeError || t('subagent.disconnected')}<button className="ml-2 underline" onClick={onRetry}>{t('subagent.retry')}</button></div>}
    <div className="relative flex min-h-0 flex-1">
      <div ref={scroll} className="min-w-0 flex-1 overflow-auto" onScroll={() => {
        if (restored.current !== position) return
        const element = scroll.current!
        position.top = element.scrollTop
        position.pinned = element.scrollHeight - element.scrollTop - element.clientHeight < 50
        setJump(!position.pinned)
      }}>
        <ChatContent>
          {detail.rounds.map(round => <RoundView key={round.id} round={round} remote={remote} onOpenFile={onOpenFile} />)}
          {!detail.rounds.length && <p className="text-xs text-muted-foreground">{t(detail.loading ? 'subagent.loading' : 'subagent.empty')}</p>}
        </ChatContent>
      </div>
      {jump && <button className="scroll-jump absolute bottom-3 left-1/2 -translate-x-1/2" title={t('chat.toBottom')} onClick={() => { position.pinned = true; scroll.current?.scrollTo({ top: scroll.current.scrollHeight }); setJump(false) }}><ChevronDown size={16} /></button>}
    </div>
    <footer className="flex shrink-0 flex-wrap items-center gap-1.5 border-t border-line/50 px-4 py-2 text-[10px] text-muted-foreground"><Eye size={12} />{t('subagent.readonly')}
      <SubagentStop agent={agent} onStop={onStop} />
    </footer>
  </section>
}

const RoundView = memo(function RoundView({ round, remote, onOpenFile }: {
  round: SubagentRound; remote: boolean; onOpenFile: (file: Artifact) => void
}) {
  const segments = useMemo(() => groupItems(round.items, round.parents), [round.items, round.parents])
  return <section className="space-y-5">
    {round.prompt && <ItemView item={{ id: `prompt:${round.id}`, kind: 'user', text: round.prompt }} />}
    {segments.map(segment => {
      const item = segment.kind === 'tools' ? segment.tools[0] : segment.kind === 'agent' ? segment.items[0] : segment.item
      const parent = round.parents[String(item.id)]
      return <div key={segKey(segment)} className={parent ? 'ml-2 border-l border-line/50 pl-3' : ''} title={parent}>
        <ChatSegment segment={segment} remote={remote} onOpenFile={onOpenFile} />
      </div>
    })}
    {round.questions.map((question, index) => <div key={index} className="rounded-lg border border-line/60 bg-canvas/70 px-3 py-3"><QuestionContent question={question} /></div>)}
    {round.error && <ItemView item={{ id: `error:${round.id}`, kind: 'notice', text: round.error }} />}
    <SubagentActivity round={round} />
  </section>
})

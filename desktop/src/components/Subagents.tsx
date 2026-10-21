import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ArrowLeft, ChevronDown, ChevronRight, Code2, ExternalLink, Eye, Maximize2, Minimize2, Sparkles, Square, X } from 'lucide-react'
import type { Artifact, Item, ToolItem, BgTask, SubagentSummary, SubagentTurnStatus, SubagentViewState } from '../types'
import { toolStatusKey } from '../toolMeta'
import type { SubagentRound, SubagentTimeline } from '../lib/subagentTimeline'
import { groupItems, segKey } from '../lib/chatSegments'
import { useI18n } from '../i18n'
import { RailSection } from './RightRail'
import { Markdown } from './Markdown'
import { ToolGroup } from './chat/ToolViews'
import { StatusIndicator } from './chat/ItemView'
import { CopyButton } from './chat/MessageActions'
import { Fold } from './ui/fold'
import { Button } from './ui/button'
import { FileCards } from './Artifacts'
import { QuestionContent } from './QuestionContent'

const subagentLabel = (agent: SubagentSummary): string =>
  agent.executor === 'codex' ? 'Codex' : agent.executor === 'claude_code' ? 'Claude Code' : 'Lumi'

function AgentMark({ agent }: { agent: SubagentSummary }) {
  if (agent.executor === 'lumi') return <span className="lumi-orb lumi-orb-idle size-3" />
  const Icon = agent.executor === 'codex' ? Code2 : Sparkles
  return <Icon size={14} className="shrink-0 text-muted-foreground" />
}
function status(agent: SubagentSummary): SubagentTurnStatus | 'idle' {
  if (agent.status !== 'idle') return agent.status
  return ['failed', 'blocked', 'interrupted', 'cancelled'].includes(agent.last_turn_status) ? agent.last_turn_status : 'idle'
}
const failed = (value: string): boolean => ['failed', 'blocked', 'interrupted'].includes(value)

export function SubagentsSection({ agents, tasks = [], selectedId, error, onSelect, onStop }: {
  agents: SubagentSummary[]; tasks?: BgTask[]; selectedId?: string | null; error: string; onSelect: (id: string) => void; onStop: (agent: SubagentSummary) => Promise<void>
}) {
  const { t } = useI18n()
  const [showIdle, setShowIdle] = useState(false)
  const working = agents.filter(agent => agent.status !== 'idle' || failed(agent.last_turn_status))
  const idle = agents.filter(agent => !working.includes(agent))
  const selectedIdle = idle.some(agent => agent.agent_id === selectedId)
  useEffect(() => { if (selectedIdle) setShowIdle(true) }, [selectedId, selectedIdle])
  const running = agents.filter(agent => agent.status === 'running').length
  const queued = agents.filter(agent => agent.status === 'queued').length
  const count = [running ? t('subagent.runningCount', { n: running }) : '', queued ? t('subagent.queuedInstances', { n: queued }) : ''].filter(Boolean).join(' · ')
  if (!agents.length && !error) return null
  return <RailSection title={t('subagent.title')} count={count || agents.length}>
    <div className="px-2 pb-2 space-y-1.5">
      {error && <p role="alert" className="px-2 text-xs text-error break-words">{error}</p>}
      {working.map(agent => <AgentCard key={agent.agent_id} agent={agent} selected={agent.agent_id === selectedId} activity={String(tasks.find(task => task.task_id === agent.active_turn_id)?.progress?.tool || '')} onSelect={onSelect} onStop={onStop} />)}
      {!!idle.length && <>
        <button className="flex items-center gap-1 w-full px-1 py-1 text-xs text-muted-foreground hover:text-ink" onClick={() => setShowIdle(!showIdle)} aria-expanded={showIdle}>
          {showIdle ? <ChevronDown size={13} /> : <ChevronRight size={13} />}{t('subagent.idleCount', { n: idle.length })}
        </button>
        {showIdle && idle.map(agent => <AgentCard key={agent.agent_id} agent={agent} selected={agent.agent_id === selectedId} onSelect={onSelect} onStop={onStop} />)}
      </>}
    </div>
  </RailSection>
}

function AgentCard({ agent, activity = '', selected = false, onSelect, onStop }: { agent: SubagentSummary; activity?: string; selected?: boolean; onSelect: (id: string) => void; onStop: (agent: SubagentSummary) => Promise<void> }) {
  const { t } = useI18n()
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState('')
  const phase = status(agent)
  const tool = activity.split(',')[0].trim()
  const toolName = ({ commandExecution: 'bash', fileChange: 'edit' } as Record<string, string>)[tool] || tool.toLowerCase()
  return <article className={`group relative rounded-lg border bg-canvas/50 ${selected ? 'border-primary/40' : 'border-line/65'}`}>
    <button onClick={() => onSelect(agent.agent_id)} className="block w-full rounded-lg px-2.5 py-2 text-left hover:bg-surface/60" title={agent.cwd}>
      <span className="flex items-center gap-1.5 pr-5"><AgentMark agent={agent} /><span className="truncate text-xs font-medium">{agent.title || agent.name}</span></span>
      <span className="mt-1 block text-[10px] text-muted-foreground">{subagentLabel(agent)} · <span className="font-mono">{agent.agent_id}</span> · {t('subagent.round', { n: agent.active_ordinal ?? agent.turn_count })}</span>
      <span className={`mt-1 flex items-center gap-1.5 text-[11px] ${failed(phase) ? 'text-error' : 'text-muted-foreground'}`}>
        {phase === 'running' && <span className="lumi-orb size-2" />}
        {t(phase === 'running' && activity ? toolStatusKey(toolName) : `subagent.status.${phase}`)}{agent.queued_count > 0 && ` · ${t('subagent.queuedCount', { n: agent.queued_count })}`}
        {phase === 'idle' && ` · ${t('subagent.lastCompleted')}`}
      </span>
    </button>
    {agent.active_turn_id && <Button variant="ghost" size="icon-sm" className="absolute right-1 top-1 size-6 text-muted-foreground opacity-0 group-hover:opacity-100 focus-visible:opacity-100" disabled={stopping} title={t('subagent.stop')} aria-label={t('subagent.stop')} onClick={() => {
      setStopping(true); setError('')
      void onStop(agent).catch(reason => setError(String(reason instanceof Error ? reason.message : reason))).finally(() => setStopping(false))
    }}><Square size={11} /></Button>}
    {error && <p role="alert" className="px-2 pb-2 text-xs text-error">{error}</p>}
  </article>
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
  position: SubagentViewState
}

export function SubagentDetail({ agent, detail, expanded, remote, online, onBack, onExpand, onRetry, onOpenFile, position }: DetailProps) {
  const { t } = useI18n()
  const scroll = useRef<HTMLDivElement>(null)
  const pinned = useRef(position.pinned)
  const restored = useRef<SubagentViewState | null>(null)
  const [jump, setJump] = useState(false)
  const [nativeError, setNativeError] = useState('')
  const nativeId = detail.nativeId
  const latest = detail.rounds.at(-1)?.id
  useLayoutEffect(() => {
    const element = scroll.current
    if (!element) return
    if (restored.current !== position) {
      // Wait for all history pages before applying an offset to an empty/partial viewport.
      if (detail.loading || detail.error || !detail.rounds.length) return
      element.scrollTop = position.pinned ? element.scrollHeight : position.top
      pinned.current = position.pinned
      restored.current = position
      setJump(!position.pinned)
    } else if (pinned.current) {
      element.scrollTop = element.scrollHeight
    }
  }, [position, detail.rounds, detail.loading, detail.error])
  return <section aria-label={`${subagentLabel(agent)} · ${agent.agent_id}`} className={`flex min-h-0 flex-1 flex-col overflow-hidden ${expanded ? 'bg-canvas' : 'sidebar-float rounded-card'}`}>
    <header className="titlebar-interactive flex shrink-0 items-center gap-1.5 border-b border-line/60 px-2 py-2">
      <Button variant="ghost" size={expanded ? 'sm' : 'icon-sm'} title={t(expanded ? 'subagent.backMain' : 'subagent.back')} aria-label={t(expanded ? 'subagent.backMain' : 'subagent.back')} onClick={onBack}><ArrowLeft />{expanded && <span className="text-xs">{t('subagent.backMain')}</span>}</Button>
      <div className="min-w-0 flex-1" title={agent.cwd}>
        <h2 className="truncate text-[13px] font-medium">{agent.title || agent.name}</h2>
        <p className="truncate text-[10px] text-muted-foreground">{subagentLabel(agent)} · {agent.agent_id} · {t(`subagent.status.${status(agent)}`)}</p>
      </div>
      {!remote && agent.executor === 'codex' && nativeId && window.lumi.openCodexThread && <Button variant="ghost" size="icon-sm" title={t('subagent.nativeOpen')} aria-label={t('subagent.nativeOpen')} onClick={() => { setNativeError(''); void window.lumi.openCodexThread!(nativeId).catch(error => setNativeError(String(error instanceof Error ? error.message : error))) }}><ExternalLink size={14} /></Button>}
      {nativeId && agent.executor !== 'lumi' && <span title={t('subagent.nativeId')}><CopyButton text={nativeId} /></span>}
      <Button variant="ghost" size="icon-sm" title={t(expanded ? 'subagent.shrink' : 'subagent.expand')} aria-label={t(expanded ? 'subagent.shrink' : 'subagent.expand')} onClick={onExpand}>{expanded ? <Minimize2 /> : <Maximize2 />}</Button>
      {!expanded && <Button variant="ghost" size="icon-sm" title={t('common.close')} aria-label={t('common.close')} onClick={onBack}><X /></Button>}
    </header>
    {(!online || detail.error || nativeError) && <div role="alert" className="shrink-0 px-4 py-2 text-xs text-error break-words">{detail.error || nativeError || t('subagent.disconnected')}<button className="ml-2 underline" onClick={onRetry}>{t('subagent.retry')}</button></div>}
    <div className="relative flex min-h-0 flex-1">
      <div ref={scroll} className="min-w-0 flex-1 overflow-auto px-4 py-4" onScroll={() => {
        if (restored.current !== position) return
        const element = scroll.current!
        pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 50
        position.top = element.scrollTop
        position.pinned = pinned.current
        if (!pinned.current && latest && position.folds[latest] === undefined) position.folds[latest] = true
        setJump(!pinned.current)
      }}>
        <div className={`${expanded ? 'mx-auto max-w-3xl' : ''} space-y-4 text-[13px]`}>
          {detail.rounds.map(round => <RoundView key={round.id} round={round} latest={round.id === latest} position={position} remote={remote} onOpenFile={onOpenFile} />)}
          {!detail.rounds.length && <p className="text-xs text-muted-foreground">{t(detail.loading ? 'subagent.loading' : 'subagent.empty')}</p>}
        </div>
      </div>
      {jump && <button className="scroll-jump absolute bottom-3 left-1/2 -translate-x-1/2" title={t('chat.toBottom')} onClick={() => { pinned.current = true; position.pinned = true; scroll.current?.scrollTo({ top: scroll.current.scrollHeight }); setJump(false) }}><ChevronDown size={16} /></button>}
    </div>
    <footer className="flex shrink-0 items-center gap-1.5 border-t border-line/50 px-3 py-2 text-[10px] text-muted-foreground"><Eye size={12} />{t('subagent.readonly')}<span className="ml-auto">{t(`subagent.status.${status(agent)}`)}</span></footer>
  </section>
}

const RoundView = memo(function RoundView({ round, latest, position, remote, onOpenFile }: { round: SubagentRound; latest: boolean; position: SubagentViewState; remote: boolean; onOpenFile: (file: Artifact) => void }) {
  const { t } = useI18n()
  const [override, setOverride] = useState<boolean | null>(null)
  const open = override ?? position.folds[round.id] ?? (round.status === 'running' || latest)
  const segments = useMemo(() => groupItems(round.items, round.parents), [round.items, round.parents])
  return <section>
    <button className="flex w-full items-center gap-2 py-1.5 text-left text-[11px] text-muted-foreground" onClick={() => { position.folds[round.id] = !open; setOverride(!open) }} aria-expanded={open}>
      {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}<span>{t('subagent.round', { n: round.ordinal })}</span><span className={failed(round.status) ? 'text-error' : ''}>{t(`subagent.status.${round.status}`)}</span><span className="ml-auto font-mono opacity-60">{round.id}</span>
    </button>
    <Fold open={open}>
      {open && <div className="space-y-4 pt-2">
        {round.prompt && <div className="rounded-lg border border-primary/20 bg-primary/[0.035] px-3 py-2.5"><div className="mb-1 text-[10px] text-muted-foreground">Lumi → {t('subagent.title')}</div><p className="whitespace-pre-wrap break-words leading-relaxed">{round.prompt}</p></div>}
        {segments.map(segment => {
          let item: Item
          let content: ReactNode = null
          if (segment.kind === 'item') {
            item = segment.item
            if (item.kind === 'assistant') content = <div className="break-words leading-relaxed"><Markdown streaming={item.streaming}>{item.text}</Markdown></div>
          } else if (segment.kind === 'files') {
            item = segment.item
            content = <FileCards files={segment.files} onOpen={onOpenFile} remote={remote} />
          } else {
            const tools: ToolItem[] = segment.kind === 'tools' ? segment.tools : segment.items
            item = tools[0]
            content = <ToolGroup tools={tools} />
          }
          const parent = round.parents[String(item.id)]
          return <div key={segKey(segment)} className={parent ? 'ml-2 border-l border-line/50 pl-3' : ''} title={parent}>{content}</div>
        })}
        {round.questions.map((question, index) => <div key={index} className="rounded-lg border border-line/60 bg-canvas/70 px-3 py-3"><QuestionContent question={question} /></div>)}
        {round.error && <p role="alert" className="whitespace-pre-wrap break-words text-xs text-error">{round.error}</p>}
        <StatusIndicator
          items={round.items}
          running={round.status === 'running'}
          waiting={false}
          streaming={round.streaming}
          thinkingText={round.thinking}
          compacting={false}
        />
      </div>}
    </Fold>
  </section>
})

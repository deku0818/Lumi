import { createElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { I18nProvider } from '../../src/i18n'
import { ClarifyDialog } from '../../src/components/ClarifyDialog'
import { SubagentDetail, SubagentsSection } from '../../src/components/Subagents'
import { TooltipProvider } from '../../src/components/ui/tooltip'
import type { SubagentTimeline } from '../../src/lib/subagentTimeline'
import type { Question, SubagentSummary } from '../../src/types'

function Providers({ children }: { children: ReactNode }) {
  return createElement(I18nProvider, { children: createElement(TooltipProvider, { children }) })
}

export function renderDetail(timeline: SubagentTimeline, remote = false): string {
  const agent: SubagentSummary = {
    agent_id: 'agent', name: 'codex', title: 'Review', executor: 'codex', cwd: '/tmp',
    status: 'running', active_turn_id: 'one', active_ordinal: 1, queued_count: 1,
    turn_count: 2, last_turn_id: 'two', last_turn_status: 'queued', summary: '',
  }
  return renderToStaticMarkup(createElement(Providers, {
    children: createElement(SubagentDetail, {
      agent, detail: { ...timeline, nativeId: null, loading: false, error: '' },
      expanded: true, remote, online: true,
      onBack: () => {}, onExpand: () => {}, onRetry: () => {}, onOpenFile: () => {}, onStop: async () => {},
      position: { top: 0, pinned: true },
    }),
  }))
}

export function renderClarify(questions: Question[]): string {
  return renderToStaticMarkup(createElement(Providers, {
    children: createElement(ClarifyDialog, { data: { questions }, onSubmit: () => {}, onCancel: () => {} }),
  }))
}

export function renderAgents(agents: SubagentSummary[], timelines: Record<string, SubagentTimeline> = {}): string {
  return renderToStaticMarkup(createElement(Providers, {
    children: createElement(SubagentsSection, { agents, timelines, error: '', onSelect: () => {}, onStop: async () => {} }),
  }))
}

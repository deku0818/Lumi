import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { I18nProvider } from '../../src/i18n'
import { MachinesProvider } from '../../src/components/MachineTabs'
import { Sidebar } from '../../src/components/Sidebar'
import { SshConnectionBody } from '../../src/components/SshConnectionView'
import { TooltipProvider } from '../../src/components/ui/tooltip'
import type { SessionMeta, SshStatus } from '../../src/types'

const machine = { id: 'remote', name: '工作服务器', host: 'fixture-host' }
const noop = () => {}

export function renderSidebar(status: SshStatus, sessions: SessionMeta[] = []): string {
  return renderToStaticMarkup(createElement(I18nProvider, {
    children: createElement(MachinesProvider, {
      value: { machines: [machine], conn: { remote: 'connecting' }, err: {}, ssh: { remote: status }, reconnect: noop, openConnection: noop },
      children: createElement(TooltipProvider, { children: createElement(Sidebar, {
        width: 256, open: true, onToggle: noop, showTitleDrag: false,
        sessions, loadedBackends: {}, machines: [machine], channels: {}, currentKey: '',
        conn: 'connecting', backend: 'remote', workspace: '', activity: {}, projectsActive: false, scheduledActive: false,
        onSelect: noop, onNew: noop, onNewChat: noop, onNewChatIn: noop,
        onOpenProjects: noop, onOpenScheduled: noop, onOpenSettings: noop,
        onPin: noop, onRename: noop, onDelete: noop,
      }) }),
    }),
  }))
}

export function renderProgress(status: SshStatus): string {
  return renderToStaticMarkup(createElement(I18nProvider, {
    children: createElement(SshConnectionBody, {
      machine, status, question: null, error: status.phase === 'error' ? status.message : '', showHost: false,
    }),
  }))
}

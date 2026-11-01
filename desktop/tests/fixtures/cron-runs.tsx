import { renderToStaticMarkup } from 'react-dom/server'
import { I18nProvider } from '../../src/i18n'
import { RunRecords } from '../../src/components/CronRuns'
import type { CronRunEntry } from '../../src/hooks/useCronRuns'
import { CronPage } from '../../src/components/CronPage'
import type { CronActiveRun, CronJob } from '../../src/types'

export function renderRecords(entries: CronRunEntry[] | null): string {
  return renderToStaticMarkup(<I18nProvider><RunRecords entries={entries} onPick={() => {}} /></I18nProvider>)
}

export function renderPage(job: CronJob, activeRuns: Record<string, CronActiveRun[]>): string {
  return renderToStaticMarkup(<I18nProvider><CronPage api={() => undefined} initialJobId={job.id} jobs={[job]} activeRuns={activeRuns} version={0} onRefresh={() => {}} onOpenRun={() => {}} /></I18nProvider>)
}

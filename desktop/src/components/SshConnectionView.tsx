import { Check,Folder,Server,ShieldCheck,X } from 'lucide-react'
import { useEffect,useState,type JSX,type ReactNode } from 'react'
import { useI18n } from '../i18n.ts'
import type { BackendRemote,SshQuestion,SshStatus } from '../types.ts'
import { Button } from './ui/button.tsx'

export function sshStep(status: SshStatus | null, question: SshQuestion | null): number {
  if (question) return question.kind === 'prepare' ? 1 : 0
  const phase = status?.phase === 'error' ? status.stage : status?.phase
  switch (phase) {
    case 'attaching':
    case 'ready':
      return 2
    case 'checking':
    case 'installing':
    case 'starting':
      return 1
    default:
      return 0
  }
}

type AnswerQuestion = (id: string, value: string | null) => void
type SshQuestionActionProps = { question: SshQuestion; answer: AnswerQuestion }

export function SshQuestionAction({ question, answer }: SshQuestionActionProps): JSX.Element {
  const { t } = useI18n()
  // Secret inputs are inside the state card; the external submit button uses its form.
  if (question.kind === 'secret') return <Button type="submit" form={'ssh-secret-' + question.id}>{t('ssh.continue')}</Button>
  const label = question.kind === 'trust' ? 'ssh.trust' : 'ssh.installContinue'
  return <Button onClick={() => answer(question.id, 'yes')}>{t(label)}</Button>
}
type SshConnectionBodyProps = {
  machine: Pick<BackendRemote, 'name' | 'host' | 'user' | 'port' | 'dataDir' | 'authMethod'>
  status: SshStatus | null
  question: SshQuestion | null
  error: string
  answer?: AnswerQuestion
}

export function SshConnectionBody({ machine, status, question, error, answer }: SshConnectionBodyProps): JSX.Element {
  const { t } = useI18n()
  const [secret, setSecret] = useState('')
  useEffect(() => setSecret(''), [question?.id])
  const ready = status?.phase === 'ready' && !error
  const step = sshStep(status, question)
  const phase = status?.phase || 'connecting'
  const busy = !question && !error && !ready
  const dataDir = question?.dataDir || status?.dataDir || machine.dataDir || '~/.lumi'
  let title = t('ssh.state.' + phase)
  let description = t('ssh.stateHint.' + phase)
  if (error) {
    title = t('ssh.failedTitle')
    description = error
  } else if (question) {
    title = t('ssh.question.' + question.kind)
    description = question.prompt
    if (question.kind === 'prepare') {
      title = t('ssh.installTitle')
      description = t('ssh.installHint')
    } else if (question.kind === 'trust') description = t('ssh.trustHint')
  } else if (phase === 'starting' && status?.preparation === 'reuse') {
    title = t('ssh.reuseTitle')
    description = t('ssh.reuseHint')
  }

  let stateIcon: ReactNode = <Server size={17} />
  if (busy) stateIcon = <span className="lumi-orb lumi-orb-sm" />
  else if (error) stateIcon = <X size={17} />
  else if (ready) stateIcon = <Check size={17} />

  function renderStep(label: string, index: number): JSX.Element {
    const done = ready || index < step
    const current = index === step
    let state = ''
    let marker: ReactNode = index + 1
    if (done) {
      state = 'done'
      marker = <Check size={11} />
    } else if (current) {
      state = 'current'
      marker = <span className={'lumi-orb lumi-orb-sm ' + (busy ? '' : 'lumi-orb-idle')} />
    }
    return (
      <li key={label} className={state} aria-current={!ready && current ? 'step' : undefined}>
        <span className="ssh-step-marker">{marker}</span><span>{t(label)}</span>
      </li>
    )
  }

  return <div className="ssh-process">
    <div className="ssh-host-summary"><span className="ssh-host-avatar"><Server size={18} /></span><div><strong>{machine.name || machine.host}</strong><small>{machine.user ? machine.user + '@' : ''}{machine.host}{machine.port ? ' · ' + machine.port : ''}</small></div><span className="ssh-secure"><ShieldCheck size={13} />SSH</span></div>
    <ol className="ssh-steps" aria-label={t('ssh.connectionSteps')}>
      {['ssh.step.verify', 'ssh.step.prepare', 'ssh.step.connect'].map(renderStep)}
    </ol>
    <div className={'ssh-state-card ' + (error ? 'ssh-state-error' : '')} aria-live="polite">
      <h3>{stateIcon}{title}</h3>
      <p role={error ? 'alert' : undefined}>{description}</p>
      {question?.kind === 'trust' && <div className="ssh-fingerprint">{question.fingerprint || question.prompt}</div>}
      {question?.kind === 'secret' && <form id={'ssh-secret-' + question.id} onSubmit={e => { e.preventDefault(); if (secret && answer) { answer(question.id, secret); setSecret('') } }}>
        <input key={question.id} autoFocus required type="password" aria-label={t('ssh.secretValue')} autoComplete="off" value={secret} onChange={e => setSecret(e.target.value)} />
      </form>}
      {(step === 1 || question?.kind === 'prepare') && <div className="ssh-state-path"><Folder size={13} /><span>{dataDir}</span></div>}
      {question?.kind === 'prepare' && <small>{t('ssh.installOnce')}</small>}
      {phase === 'installing' && !error && <small>{t('ssh.installThenConnect')}</small>}
    </div>
    <details className="ssh-details"><summary>{t('ssh.connectionDetails')}</summary><dl><dt>{t('ssh.host')}</dt><dd>{machine.host}</dd><dt>{t('ssh.auth')}</dt><dd>{t('ssh.auth.' + (machine.authMethod || 'config'))}</dd><dt>{t('ssh.dataDir')}</dt><dd>{dataDir}</dd></dl></details>
  </div>
}

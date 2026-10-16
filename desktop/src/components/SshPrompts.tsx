import { createContext,useCallback,useContext,useEffect,useLayoutEffect,useState,type JSX,type ReactNode } from 'react'
import { useI18n } from '../i18n.ts'
import type { SshQuestion } from '../types.ts'
import { useMachines } from './MachineTabs.tsx'
import { FormModal } from './SettingsKit.tsx'
import { SshConnectionBody,SshQuestionAction } from './SshConnectionView.tsx'
import { Button } from './ui/button.tsx'

type Questions = {
  queue: SshQuestion[]
  claimed: ReadonlySet<string>
  claim: (machine: string) => () => void
  answer: (id: string, value: string | null) => void
}
const Context = createContext<Questions | null>(null)

type SshQuestionProviderProps = { children: ReactNode }

export function SshQuestionProvider({ children }: SshQuestionProviderProps): JSX.Element {
  const [queue, setQueue] = useState<SshQuestion[]>([])
  const [claimed, setClaimed] = useState<ReadonlySet<string>>(new Set())
  useEffect(() => {
    const closed = new Set<string>()
    function add(question: SshQuestion): void {
      if (closed.has(question.id)) return
      setQueue(current => {
        if (current.some(item => item.id === question.id)) return current
        return [...current, question]
      })
    }
    const unsubscribe = window.lumi.ssh.onQuestion(add)
    const close = window.lumi.ssh.onQuestionClosed(id => {
      closed.add(id)
      setQueue(current => current.filter(item => item.id !== id))
    })
    void window.lumi.ssh.questions().then(items => items.forEach(add))
    return () => {
      unsubscribe()
      close()
    }
  }, [])
  const claim = useCallback((machine: string) => {
    setClaimed(current => new Set(current).add(machine))
    return () => setClaimed(current => {
      const next = new Set(current)
      next.delete(machine)
      return next
    })
  }, [])
  const answer = useCallback((id: string, value: string | null) => {
    setQueue(current => current.filter(item => item.id !== id))
    void window.lumi.ssh.answer(id, value)
  }, [])
  return <Context.Provider value={{ queue, claimed, claim, answer }}>{children}</Context.Provider>
}
function useQuestions(): Questions {
  const value = useContext(Context)
  if (!value) throw new Error('SshQuestionProvider is required')
  return value
}
type SshQuestionState = { question: SshQuestion | null; answer: Questions['answer'] }

export function useSshQuestion(machine: string): SshQuestionState {
  const { queue, claim, answer } = useQuestions()
  useLayoutEffect(() => machine ? claim(machine) : undefined, [machine, claim])
  return { question: queue.find(item => item.machine === machine) ?? null, answer }
}

/** Reconnects without an open form still need an authentication/installation prompt. */
export function SshPrompts(): JSX.Element | null {
  const { queue, claimed, answer } = useQuestions()
  const { t } = useI18n()
  const machines = useMachines()
  const question = queue.find(item => !claimed.has(item.machine))
  if (!question) return null
  const machine = machines.find(item => item.id === question.machine)
  return <FormModal title={t('ssh.connectionTitle')} onClose={() => answer(question.id, null)} className="lumi-dialog ssh-dialog sm:max-w-[720px]" bodyClassName="max-h-[65vh]"
    footer={<><span className="ssh-footnote">{t('ssh.viaSsh')}</span><Button variant="outline" onClick={() => answer(question.id, null)}>{t('common.cancel')}</Button><SshQuestionAction question={question} answer={answer} /></>}>
    <SshConnectionBody machine={{ name: machine?.name || question.host || question.machine, host: question.host || machine?.name || question.machine }} status={null} question={question} error="" answer={answer} />
  </FormModal>
}

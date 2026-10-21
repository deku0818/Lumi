import type { ReactNode } from 'react'
import type { Question } from '../types'
import { useI18n } from '../i18n'
import { GROUP } from './StepCard'

// An empty option is the shared free-text placeholder, not a visible choice.
export const questionOptions = (question: Question) => question.options.filter(option => option.label)

export function QuestionOptionText({ option }: { option: Question['options'][number] }) {
  return <span className="min-w-0">
    <span className="text-[13.5px]">{option.label}</span>
    {option.description && <span className="mt-px block text-xs text-muted-foreground">{option.description}</span>}
  </span>
}

// The main conversation supplies interactive options; subagent history uses the same content read-only.
export function QuestionContent({ question, children }: { question: Question; children?: ReactNode }) {
  const { t } = useI18n()
  const options = questionOptions(question)
  return <>
    {question.header && <div className="mb-1 mt-0.5 text-[11px] font-semibold tracking-wide text-primary">{question.header}</div>}
    <div className="mb-3 text-[15px] font-semibold leading-snug">
      {question.question}
      {question.multiSelect && <span className="ml-1.5 text-xs font-normal text-muted-foreground">{t('clarify.multi')}</span>}
    </div>
    {children ?? (options.length > 0 && <div className={GROUP}>
      {options.map((option, index) => <div key={index} className="px-3 py-2.5"><QuestionOptionText option={option} /></div>)}
    </div>)}
  </>
}

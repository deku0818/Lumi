// ask 工具交互：一次一题（单选点选项即翻下一题，多选 / 自填点「下一个」），多题时末页总览可点回
// 修改。按后端 _format_answers 同款格式构造 answer 字符串（每行 `{question} → {labels}`）。取消发 ASK_CANCELLED。
import { useState } from 'react'
import { Check } from 'lucide-react'
import { useI18n } from '../i18n'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { GROUP, StepCard, StepReview, useStepper } from './StepCard'

const ASK_CANCELLED = '__ask_cancelled__'

interface QOption {
  label: string
  description?: string
}
interface Question {
  question: string
  header?: string
  options: QOption[]
  multiSelect?: boolean
}
interface ClarifyData {
  questions?: Question[]
}

// 末尾 label 为空的项是「自定义输入」占位，过滤掉、单独用文本框
const realOpts = (q: Question) => q.options.filter((o) => o.label)

const replaceAt = <T,>(arr: T[], i: number, value: T): T[] => arr.map((x, j) => (j === i ? value : x))

function answerLabels(q: Question, picked: Set<number>, custom: string): string[] {
  const o = realOpts(q)
  const labels = [...picked]
    .sort((a, b) => a - b)
    .map((i) => o[i]?.label)
    .filter(Boolean) as string[]
  const c = custom.trim()
  if (c && (q.multiSelect || labels.length === 0)) labels.push(c)
  return labels
}

export function ClarifyDialog({
  data,
  onSubmit,
  onCancel,
}: {
  data: ClarifyData
  onSubmit: (answer: string) => void
  onCancel: () => void
}) {
  const { t } = useI18n()
  const questions = data.questions ?? []
  const total = questions.length
  const [sel, setSel] = useState<Set<number>[]>(() => questions.map(() => new Set()))
  const [custom, setCustom] = useState<string[]>(() => questions.map(() => ''))
  const step = useStepper(total)
  const review = step.index === total

  const format = (s: Set<number>[], c: string[]): string =>
    questions.map((q, qi) => `${q.question} → ${answerLabels(q, s[qi], c[qi]).join(', ')}`).join('\n')

  const pick = (qi: number, oi: number) => {
    const multi = !!questions[qi].multiSelect
    const picked = new Set(multi ? sel[qi] : [])
    if (multi && picked.has(oi)) picked.delete(oi)
    else picked.add(oi)
    const nextSel = replaceAt(sel, qi, picked)
    setSel(nextSel)
    if (multi) return
    const nextCustom = replaceAt(custom, qi, '')
    setCustom(nextCustom)
    // 单选即答：单题直接提交，多题稍停让选中态可见再翻页
    if (total === 1) onSubmit(format(nextSel, nextCustom))
    else setTimeout(step.advance, 220)
  }

  const type = (qi: number, value: string) => {
    setCustom((p) => replaceAt(p, qi, value))
    if (value && !questions[qi].multiSelect) setSel((p) => replaceAt(p, qi, new Set()))
  }

  const q = questions[step.index]
  const answered = !review && answerLabels(q, sel[step.index], custom[step.index]).length > 0
  const nextLabel =
    total === 1
      ? t('step.submit')
      : step.editing || step.index === total - 1
        ? t('clarify.confirm')
        : t('clarify.next')

  return (
    <StepCard
      title={review ? t('clarify.reviewTitle') : t('clarify.title')}
      index={step.index}
      total={total}
      canNext={answered}
      onGo={step.go}
      onSubmit={() => onSubmit(format(sel, custom))}
      leading={
        <Button variant="ghost" onClick={onCancel} className="text-muted-foreground">
          {t('common.cancel')}
        </Button>
      }
      actions={
        <Button
          disabled={!answered}
          onClick={() => (total === 1 ? onSubmit(format(sel, custom)) : step.advance())}
        >
          {nextLabel}
        </Button>
      }
    >
      {review ? (
        <StepReview
          onPick={step.jump}
          rows={questions.map((qq, qi) => (
            <div key={qi} className="min-w-0">
              <div className="truncate text-xs text-muted-foreground">{qq.question}</div>
              <div className="mt-0.5 truncate text-[13px]">
                {answerLabels(qq, sel[qi], custom[qi]).join('、')}
              </div>
            </div>
          ))}
        />
      ) : (
        <>
          {q.header && (
            <div className="mb-1 mt-0.5 text-[11px] font-semibold tracking-wide text-primary">{q.header}</div>
          )}
          <div className="mb-3 text-[15px] font-semibold leading-snug">
            {q.question}
            {q.multiSelect && (
              <span className="ml-1.5 text-xs font-normal text-muted-foreground">{t('clarify.multi')}</span>
            )}
          </div>
          <div className={GROUP}>
            {realOpts(q).map((o, oi) => {
              const on = sel[step.index].has(oi)
              return (
                <button
                  key={oi}
                  type="button"
                  onClick={() => pick(step.index, oi)}
                  className={cn(
                    'flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors',
                    on ? 'bg-primary/10' : 'hover:bg-line/30',
                  )}
                >
                  <Indicator on={on} multi={!!q.multiSelect} />
                  <span className="min-w-0">
                    <span className="text-[13.5px]">{o.label}</span>
                    {o.description && (
                      <span className="mt-px block text-xs text-muted-foreground">{o.description}</span>
                    )}
                  </span>
                </button>
              )
            })}
            <label
              className={cn(
                'flex items-center gap-3 px-3 py-2.5 transition-colors',
                custom[step.index] && 'bg-primary/10',
              )}
            >
              <Indicator on={!!custom[step.index]} multi={!!q.multiSelect} />
              <input
                value={custom[step.index]}
                onChange={(e) => type(step.index, e.target.value)}
                placeholder={t('clarify.customPlaceholder')}
                className="min-w-0 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-muted-foreground/80"
              />
            </label>
          </div>
        </>
      )}
    </StepCard>
  )
}

function Indicator({ on, multi }: { on: boolean; multi: boolean }) {
  return (
    <span
      className={cn(
        'grid size-4 shrink-0 place-items-center border-[1.5px] transition-colors',
        multi ? 'rounded' : 'rounded-full',
        on ? 'border-primary' : 'border-separator',
        on && multi && 'bg-primary',
      )}
    >
      {on &&
        (multi ? (
          <Check className="size-3 text-primary-foreground" strokeWidth={3} />
        ) : (
          <span className="size-2 rounded-full bg-primary shadow-[0_0_6px_color-mix(in_srgb,var(--color-accent)_60%,transparent)]" />
        ))}
    </span>
  )
}

export { ASK_CANCELLED }

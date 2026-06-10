// ask 工具交互：渲染 questions（单/多选 + 自定义输入），按后端 _format_answers
// 同款格式构造 answer 字符串（每行 `{question} → {labels}`）。取消发 ASK_CANCELLED。
import { useState } from 'react'
import { useI18n } from '../i18n'
import { Button } from '@/components/ui/button'

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
  const [sel, setSel] = useState<Record<number, Set<number>>>(() =>
    Object.fromEntries(questions.map((_, i) => [i, new Set<number>()])),
  )
  const [custom, setCustom] = useState<Record<number, string>>(() =>
    Object.fromEntries(questions.map((_, i) => [i, ''])),
  )

  // 末尾 label 为空的项是「自定义输入」占位，过滤掉、单独用文本框
  const realOpts = (q: Question) => q.options.filter((o) => o.label)

  const toggle = (qi: number, oi: number, multi: boolean) => {
    setSel((prev) => {
      const s = new Set(prev[qi])
      if (multi) {
        s.has(oi) ? s.delete(oi) : s.add(oi)
      } else {
        s.clear()
        s.add(oi)
      }
      return { ...prev, [qi]: s }
    })
    if (!multi) setCustom((p) => ({ ...p, [qi]: '' }))
  }

  const format = (): string =>
    questions
      .map((q, qi) => {
        const o = realOpts(q)
        const labels = [...sel[qi]]
          .sort((a, b) => a - b)
          .map((i) => o[i]?.label)
          .filter(Boolean) as string[]
        const c = custom[qi].trim()
        if (c && (q.multiSelect || labels.length === 0)) labels.push(c)
        return `${q.question} → ${labels.join(', ')}`
      })
      .join('\n')

  return (
    <div className="border border-line rounded-2xl bg-surface/50 p-4 max-h-[70vh] overflow-auto">
      <h2 className="text-base font-semibold mb-4 flex items-center gap-2">
          <span className="text-primary">✦</span>
          {t('clarify.title')}
        </h2>

        {questions.map((q, qi) => {
          const multi = !!q.multiSelect
          return (
            <div key={qi} className="mb-4">
              {q.header && (
                <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">
                  {q.header}
                </div>
              )}
              <div className="font-medium mb-2">{q.question}</div>
              <div className="space-y-1.5">
                {realOpts(q).map((o, oi) => {
                  const checked = sel[qi].has(oi)
                  return (
                    <button
                      key={oi}
                      onClick={() => toggle(qi, oi, multi)}
                      className={`w-full text-left px-3 py-2 rounded-lg border flex items-start gap-2.5 transition ${
                        checked
                          ? 'border-primary bg-primary/10'
                          : 'border-line hover:border-separator'
                      }`}
                    >
                      <span className={checked ? 'text-primary' : 'text-muted-foreground'}>
                        {multi ? (checked ? '◉' : '○') : checked ? '●' : '○'}
                      </span>
                      <span className="flex-1">
                        <span>{o.label}</span>
                        {o.description && (
                          <span className="block text-xs text-muted-foreground mt-0.5">
                            {o.description}
                          </span>
                        )}
                      </span>
                    </button>
                  )
                })}
                <input
                  value={custom[qi]}
                  onChange={(e) => setCustom((p) => ({ ...p, [qi]: e.target.value }))}
                  placeholder={t('clarify.customPlaceholder')}
                  className="w-full bg-canvas border border-line rounded-lg px-3 py-2 text-sm outline-none focus:border-primary/60 placeholder:text-muted-foreground/60"
                />
              </div>
            </div>
          )
        })}

        <div className="flex gap-2 justify-end mt-5">
          <Button variant="outline" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          <Button onClick={() => onSubmit(format())}>{t('clarify.submit')}</Button>
        </div>
    </div>
  )
}

export { ASK_CANCELLED }

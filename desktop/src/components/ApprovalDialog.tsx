// 工具审批：一批调用逐个决定（拒绝 / 允许 即翻下一项），多个时末页总览可点回修改，提交时
// 回发与 tool_calls 同序的 decisions。刻意不给「全部允许」类批量捷径——不看就同意等于没有审批。
import { useLayoutEffect, useRef, useState } from 'react'
import { Check, Wrench } from 'lucide-react'
import { useI18n } from '../i18n'
import type { ToolCallBrief } from '../types'
import { Button } from '@/components/ui/button'
import { argText, asRecord, clip, cn } from '@/lib/utils'
import { GROUP, StepCard, StepReview, useStepper } from './StepCard'

export type Decision = 'approve' | 'reject'

interface ApprovalData {
  tool_calls?: ToolCallBrief[]
  warnings?: string[]
  boundary_violations?: string[]
}

const argEntries = (c: ToolCallBrief) => Object.entries(asRecord(c.args))

export function ApprovalDialog({
  data,
  onSubmit,
}: {
  data: ApprovalData
  onSubmit: (decisions: Decision[]) => void
}) {
  const { t } = useI18n()
  const calls = data.tool_calls ?? []
  const total = calls.length
  const [decisions, setDecisions] = useState<Decision[]>([])
  const step = useStepper(total)
  const review = step.index === total
  const current = decisions[step.index]

  const decide = (d: Decision) => {
    const next = [...decisions]
    next[step.index] = d
    setDecisions(next)
    // 单个调用无需总览，决定即提交
    if (total === 1) onSubmit(next)
    else step.advance()
  }

  return (
    <StepCard
      title={review ? t('approval.reviewTitle') : t('approval.title')}
      index={step.index}
      total={total}
      canNext={!!current}
      onGo={step.go}
      onSubmit={() => onSubmit(decisions)}
      actions={
        <>
          <Button
            variant="outline"
            onClick={() => decide('reject')}
            className={cn(current === 'reject' && 'border-error/40 bg-error/10 text-error')}
          >
            {current === 'reject' && <Check />}
            {t('approval.reject')}
          </Button>
          <Button onClick={() => decide('approve')}>
            {current === 'approve' && <Check />}
            {t('approval.allow')}
          </Button>
        </>
      }
    >
      {review ? (
        <StepReview
          onPick={step.jump}
          rows={calls.map((c, i) => (
            <div key={i} className="flex min-w-0 items-center gap-2.5">
              <span
                className={cn(
                  'inline-flex shrink-0 items-center gap-1.5 text-xs font-medium before:size-1.5 before:rounded-full before:bg-current',
                  decisions[i] === 'approve' ? 'text-success' : 'text-error',
                )}
              >
                {decisions[i] === 'approve' ? t('approval.allow') : t('approval.reject')}
              </span>
              <div className="min-w-0 flex-1 font-mono">
                <div className="truncate text-[13px] font-medium">{c.name}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {/* 摘要行只露一行：截断长值，别把整份文件内容塞进被 truncate 藏掉的文本节点 */}
                  {argEntries(c).map(([, v]) => clip(argText(v), 200)).join(' · ')}
                </div>
              </div>
            </div>
          ))}
        />
      ) : (
        <CallDetail call={calls[step.index]} data={data} />
      )}
    </StepCard>
  )
}

function CallDetail({ call, data }: { call: ToolCallBrief; data: ApprovalData }) {
  const { t } = useI18n()
  const args = argEntries(call)
  const boundary = data.boundary_violations ?? []
  return (
    <>
      <div className="mb-3 mt-0.5 flex items-center gap-2.5">
        <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-primary/15 text-primary">
          <Wrench className="size-[15px]" />
        </span>
        <div className="min-w-0">
          <div className="truncate font-mono text-[13.5px] font-semibold">{call.name}</div>
          <div className="text-xs text-muted-foreground">{t('approval.params', { n: args.length })}</div>
        </div>
      </div>
      {args.length > 0 && (
        <div className={GROUP}>
          {args.map(([k, v]) => (
            <div
              key={k}
              className="grid grid-cols-[minmax(64px,auto)_1fr] items-baseline gap-3 px-3 py-2 font-mono text-[12.5px]"
            >
              <span className="text-xs text-muted-foreground">{k}</span>
              <FoldValue text={argText(v)} />
            </div>
          ))}
        </div>
      )}
      {boundary.length > 0 && (
        <div className="mt-2.5 text-xs text-error">
          ⚠ {t('approval.boundary')}
          {boundary.join('、')}
        </div>
      )}
      {(data.warnings ?? []).map((w, i) => (
        <div key={i} className="mt-1.5 text-xs text-primary/90">
          {w}
        </div>
      ))}
    </>
  )
}

// 长参数值折叠：超过 4 行（含自动换行后的视觉行）只露前 4 行 + 淡出，点「展开全部」看完整内容。
// 展开后超高交给 StepCard 内容区整体滚动，值本身不再单独滚动（避免滚动套滚动）。
function FoldValue({ text }: { text: string }) {
  const { t } = useI18n()
  const ref = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  useLayoutEffect(() => {
    if (open) return
    const el = ref.current!
    const ro = new ResizeObserver(() => setOverflowing(el.scrollHeight > el.clientHeight + 1))
    ro.observe(el)
    return () => ro.disconnect()
  }, [open])
  const folded = !open && overflowing

  const toggleLabel = () => {
    if (open) return t('common.showLess')
    const lines = text.split('\n').length
    return lines > 4 ? t('approval.expandLines', { n: lines }) : t('approval.expand')
  }

  return (
    <div className="min-w-0">
      <div
        ref={ref}
        className={cn(
          'relative whitespace-pre-wrap leading-relaxed [overflow-wrap:anywhere]',
          !open && 'max-h-[6.5em] overflow-hidden',
          folded &&
            'after:absolute after:inset-x-0 after:bottom-0 after:h-8 after:bg-gradient-to-b after:from-transparent after:to-canvas',
        )}
      >
        {text}
      </div>
      {(folded || open) && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="mt-1 font-sans text-xs text-primary hover:underline"
        >
          {toggleLabel()}
        </button>
      )}
    </div>
  )
}

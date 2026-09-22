// 分步卡骨架（工具审批 / ask 共用）：头部光点 + 标题 + 「‹ n / N ›」翻页胶囊；内容区随高度
// 平滑伸缩、封顶半屏后整体滚动（上下还有内容时头 / 底栏出淡阴影），翻页按方向滑入；底栏发丝线分隔。
// index === total 为总览页（点行回去改，改完回总览），底栏换成「提交」。
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useI18n } from '../i18n'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { CARD_L2 } from './glass'

// 分组列表容器：参数 / 选项 / 总览行共用，项间发丝线
export const GROUP = 'overflow-hidden rounded-[10px] border border-line/60 bg-canvas/70 divide-y divide-line/60'

export function useStepper(total: number) {
  const [index, setIndex] = useState(0)
  // 从总览点回某项修改：答完直接回总览，而非顺序翻下一项
  const [editing, setEditing] = useState(false)
  return {
    index,
    editing,
    go: (i: number) => {
      setEditing(false)
      setIndex(i)
    },
    jump: (i: number) => {
      setEditing(true)
      setIndex(i)
    },
    advance: () => {
      setIndex(editing ? total : index + 1)
      setEditing(false)
    },
  }
}

export function StepCard({
  title,
  index,
  total,
  canNext,
  onGo,
  onSubmit,
  leading,
  actions,
  children,
}: {
  title: string
  index: number
  total: number
  canNext: boolean
  onGo: (i: number) => void
  onSubmit: () => void
  // 底栏左侧常驻（如 ask 的「取消」）
  leading?: ReactNode
  // 步骤页底栏右侧按钮；总览页固定为「提交」
  actions: ReactNode
  children: ReactNode
}) {
  const { t } = useI18n()
  const review = index === total
  const viewport = useRef<HTMLDivElement>(null)
  const inner = useRef<HTMLDivElement>(null)
  const [height, setHeight] = useState<number>()
  const [above, setAbove] = useState(false)
  const [below, setBelow] = useState(false)
  const prev = useRef(index)
  const dx = index === prev.current ? 0 : index > prev.current ? 14 : -14

  const measureShade = () => {
    const el = viewport.current!
    setAbove(el.scrollTop > 1)
    setBelow(el.scrollTop + el.clientHeight < el.scrollHeight - 1)
  }
  useEffect(() => {
    prev.current = index
  })
  // 翻页回到顶部：滚动容器跨页复用，不重置会停在上一项的滚动位置
  useLayoutEffect(() => {
    viewport.current!.scrollTop = 0
  }, [index])
  useLayoutEffect(() => {
    const el = inner.current!
    const ro = new ResizeObserver(() => {
      setHeight(el.offsetHeight)
      measureShade()
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <div className={cn(CARD_L2, 'overflow-hidden shadow-sm')}>
      <div
        className={cn(
          'flex items-center gap-2.5 py-3 pl-4 pr-3.5 transition-shadow',
          above && 'relative z-[1] shadow-[0_6px_10px_-8px_rgb(0_0_0/0.3)]',
        )}
      >
        <span className="lumi-orb" />
        <h2 className="text-sm font-semibold">{title}</h2>
        {total > 1 && (
          <div className="ml-auto flex h-[26px] items-center rounded-full bg-line/45 px-0.5 text-xs text-muted-foreground tabular-nums">
            <PagerButton disabled={index === 0} onClick={() => onGo(index - 1)}>
              <ChevronLeft />
            </PagerButton>
            <span className="min-w-[34px] px-1 text-center">
              {review ? t('step.total', { n: total }) : `${index + 1} / ${total}`}
            </span>
            {!review && (
              <PagerButton disabled={!canNext} onClick={() => onGo(index + 1)}>
                <ChevronRight />
              </PagerButton>
            )}
          </div>
        )}
      </div>
      <div
        ref={viewport}
        onScroll={measureShade}
        className="max-h-[50vh] overflow-y-auto transition-[height] duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)]"
        style={{ height }}
      >
        <div ref={inner} className="px-4 pb-4 pt-0.5">
          <div key={index} className="step-slide" style={{ '--step-dx': `${dx}px` } as CSSProperties}>
            {children}
          </div>
        </div>
      </div>
      <div
        className={cn(
          'flex items-center gap-2 border-t border-line/70 bg-canvas/35 px-3.5 py-2.5 transition-shadow',
          below && 'shadow-[0_-6px_10px_-8px_rgb(0_0_0/0.3)]',
        )}
      >
        {leading}
        <span className="flex-1" />
        {review ? <Button onClick={onSubmit}>{t('step.submit')}</Button> : actions}
      </div>
    </div>
  )
}

function PagerButton({
  disabled,
  onClick,
  children,
}: {
  disabled: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="grid size-[22px] place-items-center rounded-full text-foreground transition hover:bg-line/70 disabled:cursor-default disabled:opacity-25 disabled:hover:bg-transparent [&_svg]:size-3"
    >
      {children}
    </button>
  )
}

export function StepReview({ rows, onPick }: { rows: ReactNode[]; onPick: (i: number) => void }) {
  const { t } = useI18n()
  return (
    <>
      <div className="mb-2.5 mt-0.5 text-xs text-muted-foreground">{t('step.reviewHint')}</div>
      <div className={GROUP}>
        {rows.map((row, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onPick(i)}
            className="group/row flex w-full min-w-0 items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-line/30"
          >
            <div className="min-w-0 flex-1">{row}</div>
            <ChevronRight className="size-3 shrink-0 text-muted-foreground opacity-0 transition group-hover/row:translate-x-0.5 group-hover/row:opacity-100" />
          </button>
        ))}
      </div>
    </>
  )
}

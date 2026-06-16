import { useI18n } from '../i18n'
import { fmtTokens } from '@/lib/utils'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
} from '@/components/ui/dropdown-menu'

// 一轮回合后的上下文用量快照（来自 turn.complete / message.complete 的 usage）。
export interface CtxUsage {
  used: number // 当前上下文占用 = 最近一次模型调用的 input_tokens（即「输入」总量）
  output: number
  cacheRead: number // input_token_details.cache_read（0 时不展示该行）
}

const R = 8
const CIRC = 2 * Math.PI * R // ≈50.27

// 阈值档位：绿 <60% / 金 60–85% / 红 >85%（与 .demos/lumi-context-meter-real.html 一致）
type Level = 'ok' | 'warn' | 'crit'
const levelOf = (p: number): Level => (p > 0.85 ? 'crit' : p >= 0.6 ? 'warn' : 'ok')
const METER: Record<Level, string> = {
  ok: 'var(--color-success)',
  warn: 'var(--color-accent)',
  crit: 'var(--color-error)',
}

const fmt = (n: number) => n.toLocaleString('en-US')

// 上下文用量指示器：默认一粒圆环（颜色即档位），点击向上弹出明细。
// 数据未就绪（无 usage 或窗口未知）时静默不渲染。
export function ContextMeter({
  usage,
  window,
  model,
}: {
  usage: CtxUsage | undefined
  window: number
  model: string
}) {
  const { t } = useI18n()
  if (!usage || usage.used <= 0 || window <= 0) return null

  const pct = Math.min(usage.used / window, 1)
  const level = levelOf(pct)
  const meter = METER[level]
  const pctText = `${Math.round(pct * 100)}%`

  const row = (label: string, value: number, color: string) => (
    <div className="flex items-center justify-between py-[3px] text-[11.5px]">
      <span className="flex items-center gap-1.5 text-muted-foreground">
        <span className="size-2 rounded-[2px]" style={{ background: color }} />
        {label}
      </span>
      <span className="text-ink tabular-nums">{fmt(value)}</span>
    </div>
  )

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          title={t('ctx.title')}
          aria-label={t('ctx.title')}
          className="grid size-[30px] place-items-center rounded-full hover:bg-canvas/60 transition outline-none"
        >
          <span className="relative grid place-items-center" style={{ color: meter }}>
            <svg width="20" height="20" viewBox="0 0 20 20" className="-rotate-90">
              <circle cx="10" cy="10" r={R} fill="none" strokeWidth="2.4" className="stroke-line" />
              <circle
                cx="10"
                cy="10"
                r={R}
                fill="none"
                strokeWidth="2.4"
                strokeLinecap="round"
                stroke={meter}
                strokeDasharray={CIRC}
                strokeDashoffset={CIRC * (1 - pct)}
                style={{ transition: 'stroke-dashoffset .9s ease, stroke .6s ease' }}
              />
            </svg>
            {/* 临界态：圆环外圈发光呼吸（光的语言，仅图标动） */}
            {level === 'crit' && (
              <span
                className="absolute -inset-[3px] rounded-full animate-pulse"
                style={{ boxShadow: `0 0 9px color-mix(in srgb, ${meter} 75%, transparent)` }}
              />
            )}
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side="top"
        align="end"
        sideOffset={10}
        className="w-61 rounded-2xl border-line bg-panel p-3.5"
      >
        <div className="mb-3 flex items-center justify-between">
          <span className="text-[12.5px] font-semibold text-ink">{t('ctx.title')}</span>
          <span
            className="rounded-md px-1.5 py-px text-xs font-bold tabular-nums"
            style={{ color: meter, background: `color-mix(in srgb, ${meter} 14%, transparent)` }}
          >
            {pctText}
          </span>
        </div>
        <div className="mb-1 h-1.5 overflow-hidden rounded-[3px] bg-line">
          <div
            className="h-full rounded-[3px]"
            style={{
              width: `${pct * 100}%`,
              background: meter,
              transition: 'width .9s ease, background .6s ease',
            }}
          />
        </div>
        <div className="mb-3 text-[11.5px] text-muted-foreground tabular-nums">
          <b className="font-semibold text-ink">{fmtTokens(usage.used)}</b> / {fmtTokens(window)} tokens
        </div>
        {row(t('ctx.input'), usage.used, 'var(--color-info)')}
        {row(t('ctx.output'), usage.output, 'var(--color-success)')}
        {usage.cacheRead > 0 && row(t('ctx.cache'), usage.cacheRead, 'var(--color-accent-dim)')}
        <div className="my-2 h-px bg-line" />
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span>{t('ctx.model')}</span>
          <b className="font-medium text-ink">
            {model} · {fmtTokens(window)}
          </b>
        </div>
        {level === 'crit' && (
          <div
            className="mt-2.5 rounded-lg px-2.5 py-[7px] text-[11px] leading-snug text-error"
            style={{
              background: 'color-mix(in srgb, var(--color-error) 12%, transparent)',
              border: '1px solid color-mix(in srgb, var(--color-error) 35%, transparent)',
            }}
          >
            ⚠ {t('ctx.warn')}
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

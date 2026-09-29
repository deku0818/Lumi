import { memo, useEffect, useRef, useState } from 'react'
import { ChevronRight, FileText } from 'lucide-react'
import type { Item } from '../../types'
import { toolStatusKey } from '../../toolMeta'
import { fmtDuration, msgTime } from '@/lib/utils'
import { Markdown } from '../Markdown'
import { useI18n } from '../../i18n'
import { USER_BUBBLE } from './MessageActions'
import type { ToolItem } from './ToolViews'

// 会话底部常驻状态指示器（参考 Claude）：
// - 运行中：光点 + 当前阶段文案 + 本轮计时；思考阶段右侧箭头点开看流式思考
// - 中断（审批/澄清/计划）：保持显示「等待确认…」，计时继续
// - 完成：退化为无文字的静止光点，留在最后一条消息下
// 阶段优先级：等待确认 > 工具执行中 > 思考中 > 正文输出中 > 兜底「正在处理…」。
export function StatusIndicator({
  items,
  running,
  runStart,
  waiting,
  streaming,
  thinkingText,
  compacting,
}: {
  items: Item[]
  running: boolean
  runStart?: number
  waiting: boolean
  streaming: boolean
  thinkingText: string
  compacting: boolean
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [now, setNow] = useState(Date.now)
  const boxRef = useRef<HTMLPreElement>(null)
  // 计时由本轮开始时刻推算（存在会话状态里），组件重挂载不归零；waiting 期间照走
  useEffect(() => {
    if (!running) return
    setNow(Date.now())
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [running])
  const sec = runStart ? Math.max(0, Math.floor((now - runStart) / 1000)) : 0
  useEffect(() => {
    if (open && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [thinkingText, open])

  if (!running) {
    // 完成态：无文字的静止光点
    return (
      <div className="mt-2">
        <span className="lumi-orb lumi-orb-idle" />
      </div>
    )
  }

  let runningTool: ToolItem | undefined
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    // agent 工具的运行态由其专属卡片（AgentGroup）展示，底栏不再重复「正在执行子任务…」
    if (it.kind === 'tool' && !it.done && it.name !== 'agent') {
      runningTool = it
      break
    }
  }
  const thinking = !waiting && !runningTool && !streaming && !!thinkingText
  const label = waiting
    ? t('status.waiting')
    : compacting
      ? t('status.compacting')
      : runningTool
        ? t(toolStatusKey(runningTool.name))
        : thinking
          ? t('common.thinking')
          : streaming
            ? t('status.writing')
            : t('status.working')

  return (
    <div className="mt-2">
      <div className="flex items-center gap-2.5 text-muted-foreground text-sm">
        <span className="lumi-orb" />
        <span>{label}</span>
        {sec > 0 && <span className="text-xs opacity-60">· {fmtDuration(sec)}</span>}
        {thinking && (
          <button
            onClick={() => setOpen((o) => !o)}
            className="px-1.5 text-muted-foreground hover:text-ink transition-colors"
          >
            <ChevronRight
              size={13}
              className={`transition-transform ${open ? 'rotate-90' : ''}`}
            />
          </button>
        )}
      </div>
      {thinking && open && (
        <pre
          ref={boxRef}
          className="text-xs mt-1.5 ml-6 px-3 py-2 rounded-lg bg-surface/60 border border-line/60 overflow-auto max-h-28 whitespace-pre-wrap text-muted-foreground/90 leading-relaxed"
        >
          {thinkingText}
        </pre>
      )}
    </div>
  )
}

// 单独组件：useI18n 会订阅 i18n context，放进 memo 的 ItemView 会让整条历史随
// context 更新重渲染（含 ReactMarkdown 重解析），故只让这一行订阅。
function RetryHint() {
  const { t } = useI18n()
  return (
    <div className="flex items-center gap-2.5 text-muted-foreground text-sm">
      <span className="lumi-orb lumi-orb-idle" />
      <span>{t('status.retried')}</span>
    </div>
  )
}

// memo：流式期间每个 delta 都重建 items 数组，但未变更项保持对象身份，
// memo 让历史消息（尤其 ReactMarkdown 解析）不随每个 token 重渲染。
export const ItemView = memo(function ItemView({ item }: { item: Exclude<Item, { kind: 'tool' }> }) {
  if (item.kind === 'user') {
    return (
      <div className="flex flex-col items-end gap-1.5">
        {/* 消息头：发送者 · 发送时刻。渠道消息两者都有；desktop 消息只有 ts
            （stream_response 统一落库的到达时刻），只显示时间 */}
        {(item.sender || item.ts) && (
          <div className="pr-1.5 text-[10.5px] text-muted-foreground/75">
            {item.sender}
            {item.ts ? `${item.sender ? ' · ' : ''}${msgTime(item.ts)}` : ''}
          </div>
        )}
        {item.images && item.images.length > 0 && (
          <div className="flex flex-wrap gap-1.5 justify-end max-w-[80%]">
            {item.images.map((src, i) => (
              <img
                key={i}
                src={src}
                alt=""
                className="max-h-52 rounded-2xl border border-line/40 object-cover"
              />
            ))}
          </div>
        )}
        {item.files && item.files.length > 0 && (
          <div className="flex flex-wrap gap-1.5 justify-end max-w-[80%]">
            {item.files.map((f, i) => (
              <span
                key={i}
                title={f.path}
                className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs border-primary/30 bg-primary/10 text-ink"
              >
                <FileText size={12} className="shrink-0 text-primary" />
                <span className="max-w-52 truncate">{f.name}</span>
              </span>
            ))}
          </div>
        )}
        {item.text && (
          <div className={`selectable ${USER_BUBBLE} max-w-[80%] wrap-anywhere`}>
            {item.text}
          </div>
        )}
      </div>
    )
  }
  if (item.kind === 'assistant') {
    return (
      <div className="md md-serif">
        <Markdown>{item.text}</Markdown>
      </div>
    )
  }
  if (item.kind === 'retry') {
    return <RetryHint />
  }
  return (
    <div className="selectable text-sm text-error/80 bg-error/5 rounded-xl px-3.5 py-2.5">
      {item.text}
    </div>
  )
})

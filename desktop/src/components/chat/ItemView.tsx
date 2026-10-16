import { memo, useEffect, useRef, useState } from 'react'
import { ChevronRight, FileText } from 'lucide-react'
import type { AttachedFile, Item } from '../../types'
import { toolStatusKey } from '../../toolMeta'
import { fmtDuration, msgTime } from '@/lib/utils'
import { Markdown } from '../Markdown'
import { useI18n } from '../../i18n'
import { USER_BUBBLE } from './MessageActions'
import type { ToolItem } from './ToolViews'
import { Fold } from '../ui/fold'

const seenArrivals = new WeakSet<object>()

// 会话底部常驻状态指示器（参考 Claude）：
// - 运行中：光点 + 当前阶段文案 + 本轮计时；思考阶段右侧箭头点开看流式思考
// - 中断（审批/澄清/计划）：保持显示「等待确认…」，计时继续
// - 无活动时隐藏；后台任务继续运行时保留入口，历史由右栏查看
// 阶段优先级：等待确认 > 工具执行中 > 思考中 > 正文输出中 > 兜底「正在处理…」。
export function StatusIndicator({
  items,
  running,
  runStart,
  waiting,
  streaming,
  thinkingText,
  compacting,
  backgroundCount = 0,
  onOpenTasks,
  failed = false,
}: {
  items: Item[]
  running: boolean
  runStart?: number
  waiting: boolean
  streaming: boolean
  thinkingText: string
  compacting: boolean
  backgroundCount?: number
  onOpenTasks?: () => void
  failed?: boolean
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

  if (!running && !backgroundCount && !failed) return null

  let runningTool: ToolItem | undefined
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    // agent 工具的运行态由其专属卡片（AgentGroup）展示，底栏不再重复「正在执行子任务…」
    if (it.kind === 'tool' && !it.done && it.name !== 'agent') {
      runningTool = it
      break
    }
  }
  const thinking = running && !failed && !waiting && !runningTool && !streaming && !!thinkingText
  const label = failed
    ? t('status.connectionFailed')
    : !running && backgroundCount > 0
      ? t('status.background', { n: backgroundCount })
      : waiting
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
        <span className={`lumi-orb ${failed ? 'lumi-orb-error' : waiting ? 'lumi-orb-wait' : ''}`} />
        <span>{label}</span>
        {running && sec > 0 && <span className="text-xs opacity-60 tabular-nums">· {fmtDuration(sec)}</span>}
        {backgroundCount > 0 && onOpenTasks && (
          <button onClick={onOpenTasks} className="text-xs text-primary hover:underline">{t('bg.title')} · {backgroundCount}</button>
        )}
        {thinking && (
          <button
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-label={t('common.thinking')}
            className="px-1.5 text-muted-foreground hover:text-ink transition-colors"
          >
            <ChevronRight
              size={13}
              className={`transition-transform ${open ? 'rotate-90' : ''}`}
            />
          </button>
        )}
      </div>
      <Fold open={thinking && open}>
        <pre
          ref={boxRef}
          className="text-xs mt-1.5 ml-6 px-3 py-2 rounded-lg bg-surface/60 border border-line/60 overflow-auto max-h-28 whitespace-pre-wrap text-muted-foreground/90 leading-relaxed"
        >
          {thinkingText}
        </pre>
      </Fold>
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

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|svg)$/i

function FileChip({ f }: { f: AttachedFile }) {
  return (
    <span
      title={f.path}
      className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs border-primary/30 bg-primary/10 text-ink"
    >
      <FileText size={12} className="shrink-0 text-primary" />
      <span className="max-w-52 truncate">{f.name}</span>
    </span>
  )
}

const THUMB =
  'block size-30 shrink-0 overflow-hidden rounded-2xl border border-line/40 transition-shadow'

// 图片附件统一 120×120 方块（居中裁切），点开预览看原图。取不到地址（远程无连接）
// 或加载失败（断线 / 文件已删）退回文件胶囊，不出破图。
function ImageThumb({ f, src, onOpen }: { f: AttachedFile; src?: string; onOpen?: (f: AttachedFile) => void }) {
  const [failed, setFailed] = useState(false)
  if (!src || failed) return <FileChip f={f} />
  return (
    <button
      title={f.name}
      onClick={() => onOpen?.(f)}
      className={`${THUMB} cursor-zoom-in hover:shadow-[0_0_0_2px_color-mix(in_srgb,var(--color-accent)_45%,transparent),0_0_18px_color-mix(in_srgb,var(--color-accent)_25%,transparent)]`}
    >
      <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} className="size-full object-cover" />
    </button>
  )
}

// memo：流式期间每个 delta 都重建 items 数组，但未变更项保持对象身份，
// memo 让历史消息（尤其 ReactMarkdown 解析）不随每个 token 重渲染。
// fileSrc：附件路径 → 可加载地址（本地 lumi-file / 远程 lumi-remote，无连接给 undefined）；
// onOpenImage：点缩略图打开预览面板。两者须为稳定引用，否则 memo 失效。
export const ItemView = memo(function ItemView({
  item,
  fileSrc,
  onOpenImage,
}: {
  item: Exclude<Item, { kind: 'tool' }>
  fileSrc?: (path: string) => string | undefined
  onOpenImage?: (f: AttachedFile) => void
}) {
  const arrival = item.kind === 'user' || item.kind === 'assistant' ? item.arrival : undefined
  const [arriving] = useState(() => !!arrival && !seenArrivals.has(arrival))
  useEffect(() => { if (arrival) seenArrivals.add(arrival) }, [arrival])
  if (item.kind === 'user') {
    const files = item.files ?? []
    const imageFiles = files.filter((f) => IMAGE_EXT.test(f.path))
    const otherFiles = files.filter((f) => !IMAGE_EXT.test(f.path))
    return (
      <div className={`flex flex-col items-end gap-1.5 ${arriving ? 'user-arrive' : ''}`}>
        {/* 消息头：发送者 · 发送时刻。渠道消息两者都有；desktop 消息只有 ts
            （stream_response 统一落库的到达时刻），只显示时间 */}
        {(item.sender || item.ts) && (
          <div className="pr-1.5 text-[10.5px] text-muted-foreground/75">
            {item.sender}
            {item.ts ? `${item.sender ? ' · ' : ''}${msgTime(item.ts)}` : ''}
          </div>
        )}
        {/* 乐观气泡的 base64 图片（turn.start 到达即换成后端路径）+ 图片附件：同尺寸方块一行 */}
        {(item.images?.length || imageFiles.length > 0) && (
          <div className="flex flex-wrap gap-1.5 justify-end max-w-[80%]">
            {item.images?.map((src, i) => (
              <span key={i} className={THUMB}>
                <img src={src} alt="" className="size-full object-cover" />
              </span>
            ))}
            {imageFiles.map((f) => (
              <ImageThumb key={f.path} f={f} src={fileSrc?.(f.path)} onOpen={onOpenImage} />
            ))}
          </div>
        )}
        {otherFiles.length > 0 && (
          <div className="flex flex-wrap gap-1.5 justify-end max-w-[80%]">
            {otherFiles.map((f, i) => (
              <FileChip key={i} f={f} />
            ))}
          </div>
        )}
        {item.text && (
          <div
            className={`selectable ${USER_BUBBLE} max-w-[80%] wrap-anywhere ${item.landed ? 'interject-land' : ''}`}
          >
            {item.text}
          </div>
        )}
      </div>
    )
  }
  if (item.kind === 'assistant') {
    return (
      <div className="md md-serif">
        <Markdown streaming={item.streaming} animateInitial={arriving && item.streaming}>{item.text}</Markdown>
      </div>
    )
  }
  if (item.kind === 'retry') {
    return <RetryHint />
  }
  return (
    <div className="selectable flex items-start gap-2 text-sm text-error/80 bg-error/5 rounded-xl px-3.5 py-2.5">
      <span className="lumi-orb lumi-orb-error mt-1" /><span>{item.text}</span>
    </div>
  )
})

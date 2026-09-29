import { memo, useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import type { Item } from '../../types'
import { isKnownTool, summarizeTools, toolArgs, toolIcon, toolStatusKey, toolTitle } from '../../toolMeta'
import { MAX_DIFF_LINES, toolDiff, type DiffLine } from '../../diff'
import { shellTokens } from '../../shell'
import { useI18n } from '../../i18n'
import { CopyButton } from './MessageActions'

export type ToolItem = Extract<Item, { kind: 'tool' }>

// 工具（单个或多个）统一渲染为一行自然语言摘要（参考 Claude：
// "Edited 2 files, ran a command, read a file ›"）。无卡片、低调融入文本流，
// 点击展开看每个工具的细节。运行中强制展开看进度，完成后默认折叠。
// groupItems 每次产出新的数组包装，但元素身份稳定：逐元素同身份即视为未变。
// ToolGroup / AgentGroup 的 memo 比较器共用，避免流式文本期间整组（含 diff 计算）重渲染。
export const sameItems = (a: ToolItem[], b: ToolItem[]) =>
  a.length === b.length && a.every((x, i) => x === b[i])

export const ToolGroup = memo(function ToolGroup({ tools }: { tools: ToolItem[] }) {
  const { t } = useI18n()
  const running = tools.some((t) => !t.done)
  const hasError = tools.some((t) => t.error)
  // override=null 时按 hasError 决定默认展开；出错的工具组默认展开但仍可手动收起
  const [override, setOverride] = useState<boolean | null>(null)
  const open = running || (override ?? hasError)
  const summary = running
    ? (() => {
        const done = summarizeTools(
          tools.filter((tool) => tool.done),
          t,
        )
        return done ? `${done}…` : t('status.working')
      })()
    : summarizeTools(tools, t)

  return (
    <div>
      <button
        onClick={() => setOverride((o) => !(o ?? hasError))}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-ink transition"
      >
        {running && <span className="text-primary animate-pulse text-[10px]">●</span>}
        {!running && hasError && <span className="text-error text-[10px]">●</span>}
        <span className={hasError ? 'text-error' : ''}>{summary}</span>
        <ChevronRight
          size={14}
          className={`shrink-0 opacity-60 transition-transform ${open ? 'rotate-90' : ''}`}
        />
      </button>
      {open && (
        <div className="mt-1.5 ml-0.5 border-l border-line/40 pl-3 space-y-0.5">
          {tools.map((t) => (
            <ToolRow key={t.id} item={t} />
          ))}
        </div>
      )}
    </div>
  )
},
(prev, next) => sameItems(prev.tools, next.tools))

// 展开后的工具明细行。收起：图标 + 标题 + 第二行关键参数（命令着色 / 路径 / 搜索词 / 键值）
// 与非默认选项 chip，扫一眼就知道调了什么。展开：整行合成一张卡——标题行作卡头（悬停只亮卡头），
// 发丝线下是命令/路径（或键值表）、虚线下接输出或 diff；chip 挪到标题行，复制悬停才出现。
// 卡边即行边，左右不留悬空的缩进与错位。出错行默认展开。
const ToolRow = memo(function ToolRow({ item }: { item: ToolItem }) {
  const { t } = useI18n()
  const errored = !!item.error
  // edit/write 展示 diff；出错时优先展示错误输出而非 diff
  const diff = errored ? null : toolDiff(item.name, item.args)
  const args = toolArgs(item.name, item.args, t)
  const hasOutput = item.done && !!item.output
  const expandable = !!args.text || !!diff || hasOutput
  const [override, setOverride] = useState<boolean | null>(null)
  const open = expandable && (override ?? errored)
  // 展开体首次打开才挂载、之后常驻（收起动画要内容在）：成组展开时不为每行预渲染输出 / diff
  const [seen, setSeen] = useState(false)
  const Icon = toolIcon(item.name)
  const chips = <ArgChips chips={args.chips} diff={diff} />
  const argText = args.shell ? <ShellText cmd={args.text} /> : args.text
  const argBlock = args.kv ? (
    <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-3.5 gap-y-0.5 py-2 pl-3 pr-10">
      {args.kv.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="m-0 min-w-0 whitespace-pre-wrap break-all text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  ) : args.text ? (
    <pre className={`m-0 whitespace-pre-wrap break-all py-2 pr-10 text-ink ${args.shell ? 'pl-[26px]' : 'pl-3'}`}>
      {/* $ 挂在左侧留白里：多行命令的续行与首行命令对齐，而非顶格像缺字 */}
      {args.shell && <span className="-ml-[14px] select-none text-muted-foreground">$ </span>}
      {argText}
    </pre>
  ) : null
  const body = diff ? (
    <DiffView lines={diff} />
  ) : !item.done ? (
    <div className="flex items-center gap-2 px-3 py-2 font-sans text-muted-foreground">
      <span className="lumi-orb scale-75" />
      {t(toolStatusKey(item.name))}
    </div>
  ) : hasOutput ? (
    <pre
      className={`m-0 max-h-60 overflow-auto whitespace-pre-wrap break-all px-3 py-2 ${errored ? 'text-error/90' : 'text-muted-foreground/90'}`}
    >
      {item.output.slice(0, 4000)}
      {item.output.length > 4000 && '\n' + t('common.truncated')}
    </pre>
  ) : null
  return (
    <div
      className={`rounded-[10px] transition-[background-color,box-shadow,margin] duration-300 ${open ? 'my-1 bg-[color-mix(in_srgb,var(--color-ink)_4%,var(--color-canvas))] ring-1 ring-inset ring-line' : ''}`}
    >
      <button
        onClick={() => {
          if (!expandable) return
          setSeen(true)
          setOverride((o) => !(o ?? errored))
        }}
        className={`w-full px-2 py-1.5 flex items-start gap-2.5 text-left text-sm ${open ? 'rounded-t-[10px]' : 'rounded-lg'} ${expandable ? 'hover:bg-ink/5' : 'cursor-default'}`}
      >
        <Icon
          size={15}
          className={`mt-[3px] shrink-0 ${!item.done ? 'text-primary animate-pulse' : errored ? 'text-error' : 'text-muted-foreground'}`}
        />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-1.5">
            {/* 未登记工具（MCP 等）的参数已在第二行键值里，标题用工具名免重复 */}
            <span className={`truncate ${errored ? 'text-error' : 'text-ink/80'}`}>
              {isKnownTool(item.name) ? toolTitle(item.name, item.args, t) : item.name}
            </span>
            {open && chips}
          </span>
          {args.text && (
            <Fold open={!open}>
              <span className="flex min-w-0 items-center gap-1.5 pt-px">
                <span className="truncate font-mono text-xs text-ink/60">{argText}</span>
                {chips}
              </span>
            </Fold>
          )}
        </span>
        {expandable && (
          <ChevronRight
            size={13}
            className={`mt-[4px] shrink-0 text-muted-foreground transition-transform ${open ? 'rotate-90' : ''}`}
          />
        )}
      </button>
      {expandable && (
        <Fold open={open}>
          {(open || seen) && (
            <div className="group/blk relative border-t border-line/70 font-mono text-xs">
              {args.text && (
                <div className="absolute right-1 top-1 rounded-md bg-canvas/80 opacity-0 transition-opacity group-hover/blk:opacity-100">
                  <CopyButton text={args.text} />
                </div>
              )}
              {argBlock}
              {argBlock && body && <div className="border-t border-dashed border-line" />}
              {body}
            </div>
          )}
        </Fold>
      )}
    </div>
  )
})

// 高度平滑开合：grid-rows 0fr↔1fr（内容常驻 DOM 才有过渡；收起时 inert 免 Tab 进隐藏内容）
function Fold({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <span
      inert={!open}
      className={`grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] ${open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr] opacity-0'}`}
    >
      <span className="block min-h-0 overflow-hidden">{children}</span>
    </span>
  )
}

// 参数选项 chip（超时 / 后台 / 行范围 / glob…）+ edit/write 的 +/- 行数
function ArgChips({ chips, diff }: { chips: string[]; diff: DiffLine[] | null }) {
  const chip = 'shrink-0 rounded-md bg-ink/[0.06] px-1.5 font-sans text-[11px] leading-[19px] whitespace-nowrap'
  const add = diff?.filter((l) => l.kind === 'add').length
  const del = diff?.filter((l) => l.kind === 'del').length
  return (
    <>
      {chips.map((c) => (
        <span key={c} className={`${chip} text-muted-foreground`}>
          {c}
        </span>
      ))}
      {!!add && <span className={`${chip} text-success`}>+{add}</span>}
      {!!del && <span className={`${chip} text-error`}>−{del}</span>}
    </>
  )
}

function ShellText({ cmd }: { cmd: string }) {
  return shellTokens(cmd).map((tk, i) => (
    <span key={i} style={tk.hl ? { color: `var(--hl-${tk.hl})` } : undefined}>
      {tk.text}
    </span>
  ))
}

// edit/write 的行级 diff 视图：新增行绿底、删除行红底、上下文行淡显。
function DiffView({ lines }: { lines: DiffLine[] }) {
  const { t } = useI18n()
  return (
    <pre className="m-0 max-h-72 overflow-auto py-2 leading-relaxed">
      {lines.slice(0, MAX_DIFF_LINES).map((l, i) => (
        <div
          key={i}
          className={`px-3 ${l.kind === 'add' ? 'bg-success/10' : l.kind === 'del' ? 'bg-error/10' : ''}`}
        >
          <span
            className={`select-none ${l.kind === 'add' ? 'text-success' : l.kind === 'del' ? 'text-error' : 'text-muted-foreground/40'}`}
          >
            {l.kind === 'add' ? '+ ' : l.kind === 'del' ? '- ' : '  '}
          </span>
          <span className={l.kind === 'ctx' ? 'text-muted-foreground/70' : 'text-ink/90'}>{l.text || ' '}</span>
        </div>
      ))}
      {lines.length > MAX_DIFF_LINES && <div className="px-3 text-muted-foreground">{t('common.truncated')}</div>}
    </pre>
  )
}

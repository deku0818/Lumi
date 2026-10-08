import { memo, useEffect, useRef, useState } from 'react'
import { Bot, Check } from 'lucide-react'
import type { SubTool } from '../../types'
import { argStr, toolIcon, toolTitle } from '../../toolMeta'
import { CARD_L1 } from '../glass'
import { asRecord, fmtTokens } from '@/lib/utils'
import { useI18n } from '../../i18n'
import { sameItems, type ToolItem } from './ToolViews'

// 运行中卡片里最多同时显示的子工具行（旧的滚出，避免无限堆积撑开主流）
const SUBAGENT_WINDOW = 3

// 子代理段渲染：单个 → 滚动窗口卡片（SingleAgent）；并发多个 → 合并面板（AgentFleet）。
export const AgentGroup = memo(
  function AgentGroup({ items }: { items: ToolItem[] }) {
    return items.length === 1 ? <SingleAgent item={items[0]} /> : <AgentFleet items={items} />
  },
  (prev, next) => sameItems(prev.items, next.items),
)

// 子工具数 + token 摘要。无子工具且无 token 时返回空串——历史恢复的卡片（子代理内部
// 活动不进 checkpoint）与刚启动尚未调工具的瞬间，都不显示误导性的「0 工具」。
const agentStats = (children: number, tokens: number, t: ReturnType<typeof useI18n>['t']) =>
  children || tokens
    ? `${children} ${t('subagent.tool')}${tokens ? ` · ${fmtTokens(tokens)}` : ''}`
    : ''

// 子代理 args.name（子代理类型名，如 explorer），缺失回退到序号
const agentName = (args: unknown, i: number): string =>
  argStr(asRecord(args).name) || `agent ${i + 1}`

// 子代理完成态的纯单行（不可展开）：静止光点 + 标签 + 详情 + 统计。单个与并发共用。
function DoneCard({ label, detail, stats }: { label: string; detail: string; stats: string }) {
  return (
    <div className={`${CARD_L1} flex items-center gap-2.5 px-3 py-2`}>
      <span className="lumi-orb lumi-orb-idle" />
      <span className="font-medium shrink-0">{label}</span>
      <span className="text-muted-foreground truncate flex-1">{detail}</span>
      {stats && <span className="text-muted-foreground text-xs tabular-nums shrink-0">{stats}</span>}
    </div>
  )
}

// 单个子代理卡片：运行中显示头部统计 + 最近 N 个子工具的有限滚动窗口（新行推入、旧行挤出）；
// 完成后收成纯单行（不可展开）。
function SingleAgent({ item }: { item: ToolItem }) {
  const { t } = useI18n()
  const children = item.children ?? []
  const tokens = (item.inTok ?? 0) + (item.outTok ?? 0)
  const title = toolTitle('agent', item.args, t)
  const stats = agentStats(children.length, tokens, t)

  if (item.done) {
    return <DoneCard label={t('subagent.label')} detail={title} stats={stats} />
  }
  return (
    <div className={`${CARD_L1} overflow-hidden`}>
      <div className="flex items-center gap-2.5 px-3 py-2">
        <span className="lumi-orb" />
        <span className="font-medium flex-1 truncate">{title}</span>
        {stats && <span className="text-muted-foreground text-xs tabular-nums shrink-0">{stats}</span>}
      </div>
      {children.length > 0 && <RunningWindow children={children} />}
    </div>
  )
}

// 并发子代理面板：卡片头「运行 N 个子 Agent」+ 总统计；每个 agent 一行（光点 · 名称 ·
// 当前动作 · 工具数）。全部完成后收成纯单行。
function AgentFleet({ items }: { items: ToolItem[] }) {
  const { t } = useI18n()
  const allDone = items.every((it) => it.done)
  const totalTools = items.reduce((n, it) => n + (it.children?.length ?? 0), 0)
  const totalTok = items.reduce((n, it) => n + (it.inTok ?? 0) + (it.outTok ?? 0), 0)
  const stats = agentStats(totalTools, totalTok, t)

  if (allDone) {
    const names = items.map((it, i) => agentName(it.args, i)).join(', ')
    return <DoneCard label={t('subagent.agentsDone', { n: items.length })} detail={names} stats={stats} />
  }
  return (
    <div className={`${CARD_L1} overflow-hidden`}>
      <div className="flex items-center gap-2.5 px-3 py-2">
        <span className="lumi-orb" />
        <span className="font-medium flex-1">{t('subagent.running', { n: items.length })}</span>
        {stats && <span className="text-muted-foreground text-xs tabular-nums shrink-0">{stats}</span>}
      </div>
      <div className="border-t border-line/70">
        {items.map((it, i) => (
          <FleetRow key={it.id} item={it} name={agentName(it.args, i)} />
        ))}
      </div>
    </div>
  )
}

// 并发面板单行：光点 + agent 名 + 当前动作（最后一个子工具，运行中金色高亮）+ 工具数。
function FleetRow({ item, name }: { item: ToolItem; name: string }) {
  const { t } = useI18n()
  const children = item.children ?? []
  const last = children[children.length - 1]
  const running = !item.done
  const Icon = item.done ? Check : last ? toolIcon(last.name) : Bot
  const action = item.done
    ? t('subagent.done')
    : last
      ? toolTitle(last.name, last.args, t)
      : t('common.thinking')
  return (
    <div className="flex items-center gap-2.5 px-3 py-1.5 border-t border-line/40 first:border-t-0">
      <span className={`subagent-dot ${running ? 'subagent-dot-run' : 'subagent-dot-done'}`} />
      <span className="font-medium shrink-0 w-20 truncate">{name}</span>
      <span className="flex items-center gap-1.5 text-muted-foreground text-xs flex-1 min-w-0">
        <Icon size={13} className={`shrink-0 ${running ? 'text-primary' : 'text-success/80'}`} />
        <span className="truncate">{action}</span>
      </span>
      <span className="text-muted-foreground text-[11px] tabular-nums shrink-0">
        {children.length} {t('subagent.tool')}
      </span>
    </div>
  )
}

// 运行中的有限工具窗口：只保留最近 SUBAGENT_WINDOW 行。新子工具从底部推入（subtool-enter），
// 超出窗口的最旧行标记 leaving 向上淡出收起（subtool-leave），动画结束后真正移除。
// seen 记录已入场过的 toolCallId，避免 leaving 行移除后又被重新加回。
function RunningWindow({ children }: { children: SubTool[] }) {
  const [rows, setRows] = useState<{ c: SubTool; leaving: boolean }[]>([])
  const seen = useRef<Set<string>>(new Set())

  useEffect(() => {
    setRows((rows) => {
      // 同步已显示行的最新状态（done/error）
      let next = rows.map((r) => {
        const fresh = children.find((c) => c.toolCallId === r.c.toolCallId)
        return fresh ? { ...r, c: fresh } : r
      })
      // 追加首次出现的子工具
      const added = children.filter((c) => !seen.current.has(c.toolCallId))
      added.forEach((c) => seen.current.add(c.toolCallId))
      next = [...next, ...added.map((c) => ({ c, leaving: false }))]
      // 活跃行超出窗口 → 最旧的几条标记离场
      const active = next.filter((r) => !r.leaving)
      const overflow = active.length - SUBAGENT_WINDOW
      if (overflow > 0) {
        const leave = new Set(active.slice(0, overflow).map((r) => r.c.toolCallId))
        next = next.map((r) => (leave.has(r.c.toolCallId) ? { ...r, leaving: true } : r))
      }
      return next
    })
  }, [children])

  const drop = (id: string) => setRows((rows) => rows.filter((r) => r.c.toolCallId !== id))

  // animationend 兜底：窗口后台化等场景下浏览器可能不派发离场动画结束事件，
  // 每个 leaving 行额外排一个一次性定时器移除，避免隐形僵尸行永久残留。drop 幂等。
  const scheduled = useRef<Set<string>>(new Set())
  useEffect(() => {
    for (const r of rows) {
      if (r.leaving && !scheduled.current.has(r.c.toolCallId)) {
        scheduled.current.add(r.c.toolCallId)
        window.setTimeout(() => drop(r.c.toolCallId), 320)
      }
    }
  }, [rows])

  return (
    <div className="border-t border-line/70 pl-7 pr-2.5 py-1">
      {rows.map(({ c, leaving }) => (
        <div
          key={c.toolCallId}
          className={leaving ? 'subtool-leave' : 'subtool-enter'}
          onAnimationEnd={leaving ? () => drop(c.toolCallId) : undefined}
        >
          <SubToolRow child={c} />
        </div>
      ))}
    </div>
  )
}

// 子工具行：图标 + 人类可读标题。运行中（!done）金色高亮，完成绿勾，出错红色。
function SubToolRow({ child }: { child: SubTool }) {
  const { t } = useI18n()
  const running = !child.done
  const Icon = child.done && !child.error ? Check : toolIcon(child.name)
  return (
    <div className="flex items-center gap-2.5 px-1.5 py-1 text-sm">
      <Icon
        size={15}
        className={`shrink-0 ${running ? 'text-primary' : child.error ? 'text-error' : 'text-success/80'}`}
      />
      <span className={`truncate ${running ? 'text-ink' : child.error ? 'text-error' : 'text-muted-foreground'}`}>
        {toolTitle(child.name, child.args, t)}
      </span>
    </div>
  )
}

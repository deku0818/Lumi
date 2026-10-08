// 每个工具的展示元数据（图标 + 摘要动作 + 人类可读标题提取）集中在一张表，
// 新增工具只需加一行。icon 驱动 ToolRow 图标，sum 驱动 summarizeTools 聚合（i18n 键
// tool.sum.<sum> / tool.sum.<sum>.n），title 从 args 提取非技术用户看得懂的标题。
import {
  SquareTerminal,
  FileText,
  FilePlus,
  FilePen,
  Search,
  Bot,
  ListChecks,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import type { ToolItem } from './types'
import type { Translate } from './i18n'
import { argText, asRecord, basename, clip } from '@/lib/utils'

// 文本提取小工具（toolTitle 标题提取共用；clip/basename/asRecord 在 lib/utils）
export const argStr = (v: unknown) => (typeof v === 'string' ? v : '')

type ToolMeta = {
  icon: LucideIcon
  sum: string // 摘要动作（同动作合并计数），见 tool.sum.* 词条
  status: string // 运行中的状态指示器文案 i18n key（动作级粒度）
  title: (a: Record<string, unknown>, t: Translate, name: string) => string
  // 工具行展示的参数；缺省 = 不展示（agent/todos 另有专门渲染）。未登记的工具（MCP 等）走 kvArgs
  args?: (a: Record<string, unknown>, t: Translate) => ToolArgs
}
// text：收起态第二行 / 展开块首行 / 复制内容；kv 非空时展开块改渲染键值表；shell 着色命令
type ToolArgs = { text: string; kv?: [string, string][]; shell?: boolean; chips: string[] }

const kvArgs = (a: Record<string, unknown>): ToolArgs => {
  const kv = Object.entries(a).map(([k, v]): [string, string] => [k, argText(v)])
  // 无参调用不给 kv：空键值表会在展开块里留一条空白
  return { text: kv.map(([k, v]) => `${k}=${v}`).join('  '), kv: kv.length ? kv : undefined, chips: [] }
}
const bashArgs = (a: Record<string, unknown>, t: Translate): ToolArgs => ({
  text: argStr(a.command),
  shell: true,
  chips: [
    typeof a.timeout === 'number' && a.timeout > 0 ? t('tool.timeout', { n: a.timeout }) : '',
    a.run_in_background ? t('tool.background') : '',
  ].filter(Boolean),
})
// read 的 offset 从 0 起：显示成 1 起的行号范围；只有显式传了才出 chip
const readArgs = (a: Record<string, unknown>, t: Translate): ToolArgs => {
  const from = (typeof a.offset === 'number' ? a.offset : 0) + 1
  const range =
    typeof a.limit === 'number' ? `L${from}–${from + a.limit - 1}` : typeof a.offset === 'number' ? `L${from}–` : ''
  return {
    text: argStr(a.file_path),
    chips: [range, argStr(a.pages) && t('tool.pages', { p: argStr(a.pages) })].filter(Boolean),
  }
}
const editArgs = (a: Record<string, unknown>, t: Translate): ToolArgs => ({
  text: argStr(a.file_path),
  chips: a.replace_all ? [t('tool.replaceAll')] : [],
})
const searchArgs = (a: Record<string, unknown>, t: Translate): ToolArgs => ({
  text: [argStr(a.pattern), argStr(a.path) === '.' ? '' : argStr(a.path)].filter(Boolean).join('  ·  '),
  chips: [argStr(a.glob), argStr(a.type), a.case_insensitive ? t('tool.ignoreCase') : ''].filter(Boolean),
})
const fileTitle = (a: Record<string, unknown>, _t: Translate, name: string) =>
  argStr(a.file_path) ? basename(argStr(a.file_path)) : name
const searchTitle = (a: Record<string, unknown>, t: Translate) =>
  argStr(a.pattern) ? t('tool.title.searchFor', { q: clip(argStr(a.pattern), 48) }) : t('tool.title.search')

const TOOL_META: Record<string, ToolMeta> = {
  bash: { icon: SquareTerminal, sum: 'command', status: 'status.runCommand', title: (a, t) => clip(argStr(a.description) || t('tool.title.command')), args: bashArgs },
  read: { icon: FileText, sum: 'read', status: 'status.readFile', title: fileTitle, args: readArgs },
  write: { icon: FilePlus, sum: 'write', status: 'status.editFile', title: fileTitle, args: editArgs },
  edit: { icon: FilePen, sum: 'edit', status: 'status.editFile', title: fileTitle, args: editArgs },
  grep: { icon: Search, sum: 'search', status: 'status.searching', title: searchTitle, args: searchArgs },
  glob: { icon: Search, sum: 'search', status: 'status.searching', title: searchTitle, args: searchArgs },
  agent: { icon: Bot, sum: 'subagent', status: 'status.subtask', title: (a, t) => clip(argStr(a.prompt) || argStr(a.name) || t('tool.title.subagent')) },
  todos: { icon: ListChecks, sum: 'todos', status: 'status.tool', title: (_a, t) => t('tool.title.todos') },
}

export const toolIcon = (name: string): LucideIcon => TOOL_META[name]?.icon ?? Wrench

// 状态行文案键（未登记的工具走通用 status.tool）
export const toolStatusKey = (name: string): string =>
  TOOL_META[name]?.status ?? 'status.tool'

// 是否是登记过的内置工具：未登记的（MCP 等）标题栏直接显示工具名
export const isKnownTool = (name: string): boolean => name in TOOL_META

// 聚合成「编辑了 2 个文件，运行了 1 条命令」/ "Edited 2 files, ran a command" 式摘要：
// 同动作合并计数（未登记的工具按工具名各自成组），首个短语之后句中小写（英文）。
export function summarizeTools(tools: ToolItem[], t: Translate): string {
  const groups = new Map<string, { sum: string; name: string; n: number }>()
  for (const tool of tools) {
    const sum = TOOL_META[tool.name]?.sum
    const key = sum ?? `other:${tool.name}`
    const g = groups.get(key) ?? { sum: sum ?? 'other', name: sum ? '' : tool.name, n: 0 }
    groups.set(key, { ...g, n: g.n + 1 })
  }
  const phrases = [...groups.values()].map(({ sum, name, n }) =>
    t(n === 1 ? `tool.sum.${sum}` : `tool.sum.${sum}.n`, { n, name }))
  return phrases
    .map((p, i) => (i === 0 ? p : p.charAt(0).toLowerCase() + p.slice(1)))
    .join(t('tool.sum.sep'))
}

// 从工具 args 提取人类可读标题（非技术用户看得懂），而非 dump raw JSON。
// 提取规则定义在 TOOL_META[name].title；未知工具回退到第一个字符串字段（子代理行只有标题，
// 靠它保留信息；主流 ToolRow 有第二行键值，对未知工具改用工具名，见 ToolRow）。
export function toolTitle(name: string, args: unknown, t: Translate): string {
  const a = asRecord(args)
  const m = TOOL_META[name]
  if (m) return m.title(a, t, name)
  const first = Object.values(a).find((v) => typeof v === 'string')
  return first ? clip(String(first)) : name
}

// 工具行展示的参数：登记了的按 TOOL_META[name].args 提取，未登记的（MCP 等）全量键值
export function toolArgs(name: string, args: unknown, t: Translate): ToolArgs {
  const m = TOOL_META[name]
  const a = asRecord(args)
  return m ? (m.args?.(a, t) ?? { text: '', chips: [] }) : kvArgs(a)
}

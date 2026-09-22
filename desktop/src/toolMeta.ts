// 每个工具的展示元数据（图标 + 动作动词/名词 + 人类可读标题提取）集中在一张表，
// 新增工具只需加一行。icon 驱动 ToolRow 图标，verb/noun 驱动 summarizeTools 聚合，
// title 从 args 提取非技术用户看得懂的标题。
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
  verb: string
  noun: string
  status: string // 运行中的状态指示器文案 i18n key（动作级粒度）
  title: (a: Record<string, unknown>, name: string) => string
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
const fileTitle = (a: Record<string, unknown>, name: string) =>
  argStr(a.file_path) ? basename(argStr(a.file_path)) : name
const searchTitle = (a: Record<string, unknown>) =>
  argStr(a.pattern) ? `Search ${clip(argStr(a.pattern), 48)}` : 'Search'

const TOOL_META: Record<string, ToolMeta> = {
  bash: { icon: SquareTerminal, verb: 'Ran', noun: 'command', status: 'status.runCommand', title: (a) => clip(argStr(a.description) || 'Run command'), args: bashArgs },
  read: { icon: FileText, verb: 'Read', noun: 'file', status: 'status.readFile', title: fileTitle, args: readArgs },
  write: { icon: FilePlus, verb: 'Wrote', noun: 'file', status: 'status.editFile', title: fileTitle, args: editArgs },
  edit: { icon: FilePen, verb: 'Edited', noun: 'file', status: 'status.editFile', title: fileTitle, args: editArgs },
  grep: { icon: Search, verb: 'Searched', noun: '', status: 'status.searching', title: searchTitle, args: searchArgs },
  glob: { icon: Search, verb: 'Searched', noun: '', status: 'status.searching', title: searchTitle, args: searchArgs },
  agent: { icon: Bot, verb: 'Ran', noun: 'subagent', status: 'status.subtask', title: (a) => clip(argStr(a.prompt) || argStr(a.name) || 'Run subagent') },
  todos: { icon: ListChecks, verb: 'Updated', noun: 'todo', status: 'status.tool', title: () => 'Update todos' },
}

export const toolIcon = (name: string): LucideIcon => TOOL_META[name]?.icon ?? Wrench

// 状态行文案键（未登记的工具走通用 status.tool）
export const toolStatusKey = (name: string): string =>
  TOOL_META[name]?.status ?? 'status.tool'

// 是否是登记过的内置工具：未登记的（MCP 等）标题栏直接显示工具名
export const isKnownTool = (name: string): boolean => name in TOOL_META

const toolAction = (name: string): { verb: string; noun: string } => {
  const m = TOOL_META[name]
  return m ? { verb: m.verb, noun: m.noun } : { verb: 'Used', noun: name }
}

// 聚合成 "Edited 2 files, ran a command, read a file" 式自然语言摘要：
// 同动作合并计数，首个短语首字母大写、其余句中小写。
export function summarizeTools(tools: ToolItem[]): string {
  if (tools.length === 0) return ''
  const order: string[] = []
  const agg = new Map<string, { verb: string; noun: string; n: number }>()
  for (const t of tools) {
    const a = toolAction(t.name)
    const key = `${a.verb}|${a.noun}`
    if (!agg.has(key)) {
      agg.set(key, { ...a, n: 0 })
      order.push(key)
    }
    agg.get(key)!.n++
  }
  const phrases = order.map((k) => {
    const { verb, noun, n } = agg.get(k)!
    if (!noun) return n === 1 ? verb : `${verb} ${n} times`
    return n === 1 ? `${verb} a ${noun}` : `${verb} ${n} ${noun}s`
  })
  return phrases
    .map((p, i) => (i === 0 ? p : p.charAt(0).toLowerCase() + p.slice(1)))
    .join(', ')
}

// 从工具 args 提取人类可读标题（非技术用户看得懂），而非 dump raw JSON。
// 提取规则定义在 TOOL_META[name].title；未知工具回退到第一个字符串字段（子代理行只有标题，
// 靠它保留信息；主流 ToolRow 有第二行键值，对未知工具改用工具名，见 ToolRow）。
export function toolTitle(name: string, args: unknown): string {
  const a = asRecord(args)
  const m = TOOL_META[name]
  if (m) return m.title(a, name)
  const first = Object.values(a).find((v) => typeof v === 'string')
  return first ? clip(String(first)) : name
}

// 工具行展示的参数：登记了的按 TOOL_META[name].args 提取，未登记的（MCP 等）全量键值
export function toolArgs(name: string, args: unknown, t: Translate): ToolArgs {
  const m = TOOL_META[name]
  const a = asRecord(args)
  return m ? (m.args?.(a, t) ?? { text: '', chips: [] }) : kvArgs(a)
}

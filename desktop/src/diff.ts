// 工具 diff 视图：从 edit/write 的 args 在前端就地算出行级 diff，无需后端推送。
// edit 是"局部替换"，用公共前后缀裁剪保留首尾未变行作上下文、中间作 -旧/+新；
// 不引入完整 LCS——对定向替换场景已足够清晰，且零依赖、易读。

export type DiffLine = { kind: 'ctx' | 'add' | 'del'; text: string }

// 渲染上限（DiffView 截断并提示）；toolDiff 返回完整 diff，+/- 计数按全量算
export const MAX_DIFF_LINES = 240
// 改动块前后保留的未改动行数，其余折成一行「⋯ N 行未改动」
export const DIFF_CONTEXT = 3

export type DiffRow = DiffLine | { kind: 'gap'; n: number }

// 显示用折叠：长文件里改动前的公共前缀可能远超渲染上限，不折叠的话改动本身被截在
// 视野外。只折掉多于 1 行的部分——「⋯ 1 行未改动」不如直接显示那一行
export function foldContext(lines: DiffLine[]): DiffRow[] {
  const first = lines.findIndex((l) => l.kind !== 'ctx')
  if (first < 0) return lines
  let last = lines.length - 1
  while (lines[last].kind === 'ctx') last--
  const from = first - DIFF_CONTEXT > 1 ? first - DIFF_CONTEXT : 0
  const to = lines.length - (last + 1 + DIFF_CONTEXT) > 1 ? last + 1 + DIFF_CONTEXT : lines.length
  return [
    ...(from > 0 ? [{ kind: 'gap', n: from } as const] : []),
    ...lines.slice(from, to),
    ...(to < lines.length ? [{ kind: 'gap', n: lines.length - to } as const] : []),
  ]
}

// 公共前后缀裁剪的轻量行级 diff（old → new）
function diffLines(oldText: string, newText: string): DiffLine[] {
  const a = oldText.split('\n')
  const b = newText.split('\n')
  let pre = 0
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++
  let suf = 0
  while (
    suf < a.length - pre &&
    suf < b.length - pre &&
    a[a.length - 1 - suf] === b[b.length - 1 - suf]
  )
    suf++
  const out: DiffLine[] = []
  for (let i = 0; i < pre; i++) out.push({ kind: 'ctx', text: a[i] })
  for (let i = pre; i < a.length - suf; i++) out.push({ kind: 'del', text: a[i] })
  for (let i = pre; i < b.length - suf; i++) out.push({ kind: 'add', text: b[i] })
  for (let i = a.length - suf; i < a.length; i++) out.push({ kind: 'ctx', text: a[i] })
  return out
}

// 工具调用是否可渲染为 diff：edit 取 old/new_string，write 视为全新增。无则返回 null。
export function toolDiff(name: string, args: unknown): DiffLine[] | null {
  const a = (args && typeof args === 'object' ? args : {}) as Record<string, unknown>
  if (name === 'edit' && typeof a.old_string === 'string' && typeof a.new_string === 'string') {
    return diffLines(a.old_string, a.new_string)
  }
  if (name === 'write' && typeof a.content === 'string') {
    return a.content.split('\n').map((text) => ({ kind: 'add', text }) as DiffLine)
  }
  return null
}

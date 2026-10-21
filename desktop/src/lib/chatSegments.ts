import type { Artifact, Item, ToolItem } from '../types.ts'

export type Segment =
  | { kind: 'tools'; tools: ToolItem[] }
  | { kind: 'agent'; items: ToolItem[] }
  | { kind: 'files'; item: ToolItem; files: Artifact[] }
  | { kind: 'item'; item: Exclude<Item, { kind: 'tool' }> }

// 主会话与子会话共用；不同嵌套来源不合并。
// 把连续的 tool item 合并成一段，其余 item 各自独立 —— 用于工具分组渲染。
// 子代理（name==='agent'）也按连续合并成一段：单个渲染成滚动窗口卡片，并发多个
// 渲染成一张「N 个子 Agent」面板（每行一个 agent）。
export function groupItems(items: Item[], parents: Readonly<Record<string, string>> = {}): Segment[] {
  const segs: Segment[] = []
  let previousParent: string | undefined
  for (const it of items) {
    const parent = parents[String(it.id)] ?? ''
    const last = parent === previousParent ? segs.at(-1) : undefined
    previousParent = parent
    if (it.kind === 'tool' && it.name === 'agent') {
      if (last?.kind === 'agent') last.items.push(it)
      else segs.push({ kind: 'agent', items: [it] })
    } else if (it.kind === 'tool' && (it.name === 'artifacts' || it.name === 'present_files')) {
      // artifacts 不并入灰色工具组：单独成段，渲染成文件卡片。
      // 兼容旧名 present_files：历史 checkpoint 里存的是当时的工具名（见
      // session.py 的 _history_items 直取 tool_call.name），改名前的会话重开
      // 仍要出卡片。输出 JSON 形状未变，认名即可。
      // 在此（随 items 记忆化）解析一次 JSON，避免每次渲染都 parse。
      segs.push({ kind: 'files', item: it, files: parseArtifacts(it.output) })
    } else if (it.kind === 'tool') {
      if (last?.kind === 'tools') last.tools.push(it)
      else segs.push({ kind: 'tools', tools: [it] })
    } else {
      segs.push({ kind: 'item', item: it })
    }
  }
  return segs
}

// segment 的稳定 React key / 复制映射 key（不依赖数组下标，切片/虚拟化也不错位）
export function segKey(segment: Segment): string {
  switch (segment.kind) {
    case 'tools':
      return `g${segment.tools[0].historyGroup ?? segment.tools[0].id}`
    case 'agent':
      return `a${segment.items[0].historyGroup ?? segment.items[0].id}`
    case 'files':
      return `f${segment.item.id}`
    case 'item':
      return `i${segment.item.id}`
  }
}

// artifacts 工具输出（JSON 字符串）→ 文件列表；非法 JSON 退化为空。
function parseArtifacts(output: string): Artifact[] {
  try {
    const data = JSON.parse(output)
    return Array.isArray(data) ? data : []
  } catch {
    return []
  }
}

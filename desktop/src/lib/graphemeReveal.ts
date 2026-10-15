import type { Element, Root, RootContent, Text } from 'hast'

const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' })

// 用原始 Markdown 的 offset 判断新增内容；格式重解析不会把旧文本当作新回复。
// 只包装新增字素，代码与高亮 DOM 保持原样。大批 delta 的级联延迟封顶，避免积压。
export function revealNewText(tree: Root, from: number): void {
  let index = 0
  function split(node: Text): RootContent[] {
    const start = node.position?.start.offset
    if (start === undefined || start + node.value.length <= from) return [node]
    const children: RootContent[] = []
    let prefix = ''
    for (const part of segmenter.segment(node.value)) {
      if (start + part.index < from) {
        prefix += part.segment
        continue
      }
      if (prefix) { children.push({ type: 'text', value: prefix }); prefix = '' }
      children.push({
        type: 'element', tagName: 'span',
        properties: { className: ['grapheme-reveal'], style: `animation-delay:${Math.min(index++ * 30, 180)}ms` },
        position: node.position && {
          start: { ...node.position.start, offset: start + part.index },
          end: node.position.end,
        },
        children: [{ type: 'text', value: part.segment }],
      })
    }
    if (prefix) children.push({ type: 'text', value: prefix })
    return children
  }
  function walk(parent: Root | Element): void {
    if (parent.type === 'element' && ['pre', 'code'].includes(parent.tagName)) return
    parent.children = parent.children.flatMap(node => {
      if (node.type === 'text') return split(node)
      if (node.type === 'element') walk(node)
      return [node]
    }) as typeof parent.children
  }
  walk(tree)
}

export function rehypeReveal(options: { from: number }) {
  return (tree: Root): void => revealNewText(tree, options.from)
}

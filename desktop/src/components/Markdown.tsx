import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

// 所有 markdown 渲染的单一入口：插件配置（GFM + 代码高亮）集中在此，
// 日后改插件 / 加 sanitize 只动这一处，避免在多个调用点漂移。
export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
      {children}
    </ReactMarkdown>
  )
}

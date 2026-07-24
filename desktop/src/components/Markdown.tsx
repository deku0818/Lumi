import { useMemo, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

// 无协议相对链接点击会把整个窗口导航走导致应用重载，链接按去向分流：
// #fragment 页内滚动；带协议的（http/mailto/vscode…）经 target=_blank 走
// setWindowOpenHandler 到系统应用；裸 .md 文件名在调用方传入 onFileLink 时
// 是应用内跳转（记忆浮层 wiki 式互链，此时外链带 ↗ 角标区分去向）；
// 其余相对路径降级为普通文本。
const SCHEME = /^[a-z][a-z0-9+.-]*:/i

// href 被 mdast normalizeUri percent-encode 过，还原后才是真实文件名（CJK 等）；
// 调用方已排除 #fragment 和带协议链接。normalizeUri 会放过 %zz 这类非法序列，decode 会抛
function memoryFileName(href: string): string | null {
  if (href.includes('/')) return null
  try {
    const name = decodeURIComponent(href)
    return name.endsWith('.md') ? name : null
  } catch {
    return null
  }
}

function MdLink({
  href,
  title,
  children,
  onFileLink,
}: {
  href?: string
  title?: string
  children?: ReactNode
  onFileLink?: (name: string) => void
}) {
  if (href) {
    if (href.startsWith('#')) {
      return (
        <a href={href} title={title}>
          {children}
        </a>
      )
    }
    if (SCHEME.test(href)) {
      return (
        <a href={href} target="_blank" rel="noreferrer" title={title}>
          {children}
          {onFileLink && <span className="text-[0.8em] opacity-70">↗</span>}
        </a>
      )
    }
    if (onFileLink) {
      const name = memoryFileName(href)
      if (name) {
        return (
          <a
            href="#"
            title={title}
            onClick={(e) => {
              e.preventDefault()
              onFileLink(name)
            }}
          >
            {children}
          </a>
        )
      }
    }
  }
  return <span title={title}>{children}</span>
}

// 所有 markdown 渲染的单一入口：插件配置（GFM + 代码高亮）集中在此，
// 日后改插件 / 加 sanitize 只动这一处，避免在多个调用点漂移。
// components 用 useMemo 稳住组件身份——否则流式渲染时每个 token 都会
// 让 React 视 a 为新类型而全量卸载重建消息里的所有链接 DOM。
export function Markdown({
  children,
  onFileLink,
}: {
  children: string
  onFileLink?: (name: string) => void
}) {
  const components = useMemo(
    () => ({
      a: (p: { href?: string; title?: string; children?: ReactNode }) => (
        <MdLink {...p} onFileLink={onFileLink} />
      ),
    }),
    [onFileLink],
  )
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={components}>
      {children}
    </ReactMarkdown>
  )
}

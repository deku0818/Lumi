import { Children, isValidElement, useLayoutEffect, useMemo, useRef, type ComponentProps, type ReactNode } from 'react'
import ReactMarkdown, { type ExtraProps } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { rehypeReveal } from '../lib/graphemeReveal'
import { CopyButton } from './chat/MessageActions'

function codeText(children: ReactNode): string {
  return Children.toArray(children).map(child =>
    typeof child === 'string' || typeof child === 'number' ? String(child)
      : isValidElement<{ children?: ReactNode }>(child) ? codeText(child.props.children) : '',
  ).join('')
}

function CodeBlock({ children }: ComponentProps<'pre'>) {
  const code = Children.toArray(children).find(isValidElement<{ className?: string }>)
  const language = isValidElement<{ className?: string }>(code)
    ? code.props.className?.match(/language-([\w+-]+)/)?.[1] : undefined
  return (
    <div className="md-code-block">
      <div className="md-code-header"><span>{language || 'code'}</span><CopyButton text={codeText(children)} /></div>
      <pre>{children}</pre>
    </div>
  )
}

// Markdown 重解析可能复用 tail 的 span；以源 offset 给内部元素换 key，确保只播新字素。
function RevealSpan({ node, children, ...props }: ComponentProps<'span'> & ExtraProps) {
  return <span key={node?.position?.start.offset} {...props}>{children}</span>
}

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
  streaming = false,
  animateInitial = false,
}: {
  children: string
  onFileLink?: (name: string) => void
  streaming?: boolean
  animateInitial?: boolean
}) {
  // 首次实时出现可包括首段；已展示内容重挂载以现有文本为基线，不重播历史。
  const previous = useRef(animateInitial ? '' : children)
  const from = children.startsWith(previous.current) ? previous.current.length : children.length
  useLayoutEffect(() => { previous.current = children }, [children])
  const plugins = useMemo(() => streaming
    ? [rehypeHighlight, [rehypeReveal, { from }] as [typeof rehypeReveal, { from: number }]]
    : [rehypeHighlight], [streaming, from])
  const components = useMemo(
    () => ({
      a: (p: { href?: string; title?: string; children?: ReactNode }) => (
        <MdLink {...p} onFileLink={onFileLink} />
      ),
      pre: CodeBlock,
      span: RevealSpan,
    }),
    [onFileLink],
  )
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={plugins} components={components}>
      {children}
    </ReactMarkdown>
  )
}

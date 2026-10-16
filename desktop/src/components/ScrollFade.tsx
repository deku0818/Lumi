import { useCallback,useLayoutEffect,useRef,useState,type CSSProperties,type JSX,type ReactNode } from 'react';

type ScrollFadeProps = { children: ReactNode; className?: string }

/** Fade only the edges that have more content beyond the visible list. */
export function ScrollFade({ children, className = '' }: ScrollFadeProps): JSX.Element {
  const viewport = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ top: false, bottom: false })
  const measure = useCallback(() => {
    const el = viewport.current
    if (!el) return
    const next = { top: el.scrollTop > 1, bottom: el.scrollHeight - el.clientHeight - el.scrollTop > 1 }
    setEdges(current => current.top === next.top && current.bottom === next.bottom ? current : next)
  }, [])
  useLayoutEffect(() => {
    const observer = new ResizeObserver(measure)
    observer.observe(viewport.current!)
    observer.observe(content.current!)
    measure()
    return () => observer.disconnect()
  }, [measure])
  return <div ref={viewport} onScroll={measure} className={'lumi-scroll-fade ' + className}
    style={{ '--fade-top': edges.top ? '18px' : '0px', '--fade-bottom': edges.bottom ? '28px' : '0px' } as CSSProperties}>
    <div ref={content}>{children}</div>
  </div>
}

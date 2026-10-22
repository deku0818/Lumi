import { useEffect, useMemo, useRef, useState, type JSX } from 'react'
import { useI18n } from '../i18n'
import { homeGreetingFrame } from '../lib/homeGreeting'

const PHRASE_KEYS = ['ssh.welcome', 'home.greeting.idea', 'home.greeting.thoughts', 'home.greeting.write', 'home.greeting.plan', 'home.greeting.chat']

export function HomeGreeting({ paused }: { paused: boolean }): JSX.Element {
  const { lang, t } = useI18n()
  const phrases = useMemo(() => PHRASE_KEYS.map(key => t(key)), [lang])
  return <TypewriterGreeting key={lang} phrases={phrases} typingMs={lang === 'zh' ? 90 : 50} paused={paused} />
}

function TypewriterGreeting({ phrases, typingMs, paused }: {
  phrases: readonly string[]
  typingMs: number
  paused: boolean
}): JSX.Element {
  const elapsed = useRef(0)
  const [frame, setFrame] = useState(() => homeGreetingFrame(phrases, 0, typingMs))
  const [reduced, setReduced] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches)
  const [visible, setVisible] = useState(() => !document.hidden)
  const stopped = paused || reduced || !visible

  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)')
    const onMotion = () => setReduced(media.matches)
    const onVisibility = () => setVisible(!document.hidden)
    media.addEventListener('change', onMotion)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      media.removeEventListener('change', onMotion)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  useEffect(() => {
    if (stopped) return
    const start = performance.now()
    let timer: ReturnType<typeof setTimeout>
    function tick(): void {
      const next = homeGreetingFrame(phrases, elapsed.current + performance.now() - start, typingMs)
      setFrame(next)
      timer = setTimeout(tick, next.nextIn)
    }
    tick()
    return () => {
      clearTimeout(timer)
      elapsed.current += performance.now() - start
    }
  }, [phrases, typingMs, stopped])

  return (
    <div className="home-greeting-wrap mb-9 text-center">
      <h1 className="home-greeting serif" aria-label={frame.fullText}>
        <span className="home-greeting-space" aria-hidden>{frame.fullText}</span>
        <span className="home-greeting-typed" aria-hidden>
          <span>{reduced ? frame.fullText : frame.text}</span>
          <i className="home-greeting-cursor" data-active={frame.cursor && !stopped} />
        </span>
      </h1>
    </div>
  )
}

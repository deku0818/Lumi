const HOME_GREETING_INTERVAL = 60_000

type GreetingFrame = {
  text: string
  fullText: string
  cursor: boolean
  nextIn: number
}

// A 方案：输入 → 静置（无光标）→ 回删；相邻两句的开始时间相隔一分钟。
export function homeGreetingFrame(phrases: readonly string[], elapsed: number, typingMs: number): GreetingFrame {
  const fullText = phrases[Math.floor(elapsed / HOME_GREETING_INTERVAL) % phrases.length]
  const letters = Array.from(fullText)
  const time = elapsed % HOME_GREETING_INTERVAL
  const deleteStart = HOME_GREETING_INTERVAL - letters.length * 30 - 450
  let count = letters.length
  let cursor = false
  let nextIn: number
  if (time < letters.length * typingMs) {
    count = Math.floor(time / typingMs)
    cursor = true
    nextIn = typingMs - time % typingMs
  } else if (time < deleteStart) {
    nextIn = deleteStart - time
  } else if (time < HOME_GREETING_INTERVAL - 450) {
    count -= Math.floor((time - deleteStart) / 30)
    cursor = true
    nextIn = 30 - (time - deleteStart) % 30
  } else {
    count = 0
    nextIn = HOME_GREETING_INTERVAL - time
  }
  return { text: letters.slice(0, count).join(''), fullText, cursor, nextIn }
}

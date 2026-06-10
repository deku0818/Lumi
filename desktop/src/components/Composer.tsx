import type { ClipboardEvent, KeyboardEvent, RefObject } from 'react'

// 带斜杠命令高亮的输入框。textarea 文字透明、只留光标；底层镜像层渲染同样的
// 文本，但把开头 highlightLen 个字符（"/命令" token）画成带底色的 accent 色。
// 两层共用 .composer-layer 排版，确保字形/换行/滚动逐像素重合。
export function Composer({
  value,
  onChange,
  onKeyDown,
  onPaste,
  disabled,
  placeholder,
  highlightLen,
  inputRef,
}: {
  value: string
  onChange: (v: string) => void
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => void
  onPaste?: (e: ClipboardEvent<HTMLTextAreaElement>) => void
  disabled: boolean
  placeholder: string
  highlightLen: number
  inputRef: RefObject<HTMLTextAreaElement | null>
}) {
  const head = value.slice(0, highlightLen)
  const tail = value.slice(highlightLen)

  // 内容超过 max-h-48 时 textarea 内部滚动，镜像层跟随同步
  const syncScroll = () => {
    const ta = inputRef.current
    const m = ta?.previousElementSibling as HTMLElement | null
    if (ta && m) {
      m.scrollTop = ta.scrollTop
      m.scrollLeft = ta.scrollLeft
    }
  }

  return (
    <div className="relative">
      <div
        aria-hidden
        className="composer-layer absolute inset-0 max-h-48 overflow-hidden pointer-events-none text-ink"
      >
        {highlightLen > 0 ? (
          <>
            <span className="rounded bg-primary/15 text-primary font-medium">{head}</span>
            {tail}
          </>
        ) : (
          value
        )}
        {'​'}
      </div>
      <textarea
        ref={inputRef}
        value={value}
        rows={1}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        onScroll={syncScroll}
        disabled={disabled}
        placeholder={placeholder}
        className="composer-layer composer relative max-h-48 overflow-auto resize-none bg-transparent text-transparent caret-ink outline-none placeholder:text-muted-foreground/50 disabled:opacity-50"
      />
    </div>
  )
}

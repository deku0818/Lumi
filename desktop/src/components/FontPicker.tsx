import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { useI18n } from '../i18n'
import { listLocalFonts, cssFamily } from '../font'

// 列表最多渲染的行数（多出的提示用搜索收窄）
const MAX_VISIBLE = 60

// 界面字体选择器：从本机已装字体里挑，每个字体名用自身字体预览。
// value='' 表示默认字体栈。fonts=null 表示尚未加载（区分「加载到空/不可用」）。
export function FontPicker({ value, onChange }: { value: string; onChange: (f: string) => void }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [fonts, setFonts] = useState<string[] | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  // 在点击处理器内同步触发首次枚举：queryLocalFonts 需要 user activation，
  // 放到 useEffect 里（点击提交后）在打包版可能丢失激活而失败。
  const toggle = () => {
    const next = !open
    setOpen(next)
    if (next && fonts === null) void listLocalFonts().then(setFonts)
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = fonts ?? []
    return q ? list.filter((f) => f.toLowerCase().includes(q)) : list
  }, [fonts, query])

  const pick = (f: string) => {
    onChange(f)
    setOpen(false)
    setQuery('')
  }

  // 封顶渲染行数，避免上百字体一次性挂载（每行各解析自身字体）造成开屏卡顿；多出的靠搜索收窄。
  const shown = filtered.slice(0, MAX_VISIBLE)
  const overflow = filtered.length - shown.length

  return (
    <div ref={ref} className="relative">
      <button
        onClick={toggle}
        className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-sm bg-canvas/60 border border-line/30 text-ink hover:bg-line/30 transition min-w-44 justify-between"
      >
        <span className="truncate" style={value ? { fontFamily: cssFamily(value) } : undefined}>
          {value || t('settings.uiFont.default')}
        </span>
        <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-1 w-64 rounded-xl border border-line/40 bg-surface shadow-lg overflow-hidden">
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}
            placeholder={t('settings.uiFont.search')}
            className="w-full px-3 py-2 text-sm bg-canvas/60 border-b border-line/30 outline-none text-ink placeholder:text-muted-foreground"
          />
          <div className="max-h-64 overflow-auto py-1">
            <Item label={t('settings.uiFont.default')} selected={!value} onClick={() => pick('')} />
            {shown.map((f) => (
              <Item key={f} label={f} font={f} selected={f === value} onClick={() => pick(f)} />
            ))}
            {fonts !== null && filtered.length === 0 && (
              <div className="px-3 py-2 text-sm text-muted-foreground">
                {t(fonts.length === 0 ? 'settings.uiFont.unavailable' : 'settings.uiFont.empty')}
              </div>
            )}
            {overflow > 0 && (
              <div className="px-3 py-1.5 text-xs text-muted-foreground">{t('settings.uiFont.more', { n: overflow })}</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function Item({
  label,
  font,
  selected,
  onClick,
}: {
  label: string
  font?: string
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center justify-between gap-2 px-3 py-1.5 text-left text-sm text-ink hover:bg-line/30 transition"
    >
      <span className="truncate" style={font ? { fontFamily: cssFamily(font) } : undefined}>
        {label}
      </span>
      {selected && <Check size={14} className="shrink-0 text-primary" />}
    </button>
  )
}

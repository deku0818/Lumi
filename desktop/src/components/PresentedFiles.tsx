import { useEffect, useState } from 'react'
import { Markdown } from './Markdown'
import {
  File,
  FileText,
  FileType,
  FileSpreadsheet,
  FileX2,
  Image as ImageIcon,
  Film,
  Music,
  Folder,
  ExternalLink,
  Copy,
  RefreshCw,
  X,
  type LucideIcon,
} from 'lucide-react'
import type { PresentedFile } from '../types'
import { useI18n } from '../i18n'

// lumi-file://local/<abs path>：固定 host=local（自定义 standard scheme 不允许空 host，
// 否则 Chromium 会把路径首段当 host 吃掉）；各路径段 encodeURIComponent，空格/中文/#/? 都安全。
// 主进程 protocol.handle('lumi-file') 解码 pathname 后读本地文件。
export const fileUrl = (p: string) => 'lumi-file://local' + p.split('/').map(encodeURIComponent).join('/')

// present_files 工具输出（JSON 字符串）→ 文件列表；非法 JSON 退化为空。
export function parsePresentedFiles(output: string): PresentedFile[] {
  try {
    const data = JSON.parse(output)
    return Array.isArray(data) ? data : []
  } catch {
    return []
  }
}

const ext = (f: PresentedFile) => (f.name || f.path).toLowerCase().split('.').pop() || ''

// 图标按 kind 选字形（不上彩色，统一 muted）
const KIND_ICON: Record<string, LucideIcon> = {
  image: ImageIcon,
  video: Film,
  audio: Music,
  sheet: FileSpreadsheet,
  pdf: FileType,
  text: FileText,
  doc: FileText,
}
const fileIcon = (f: PresentedFile): LucideIcon => KIND_ICON[f.kind || ''] || File

const fmtSize = (n?: number) => {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
const typeText = (f: PresentedFile) => (ext(f) || f.kind || 'file').toUpperCase()

// 文本类扩展名：这些走 fetch().text() 在面板里直接展示
const TEXT_EXT = new Set(
  'txt csv tsv json jsonl log xml yaml yml ini toml env py ts tsx js jsx mjs cjs css scss sass sh bash zsh rs go java kt c h cpp hpp cc rb php swift sql r lua pl conf cfg gitignore dockerfile makefile'.split(' '),
)

// 超过此大小不内嵌预览（用元数据 size 判定，不读文件）→ 显示提示 + 用系统应用打开
const MAX_PREVIEW_BYTES = 50 * 1024 * 1024

type PreviewKind = 'image' | 'pdf' | 'html' | 'markdown' | 'text' | 'none'
function previewKind(f: PresentedFile): PreviewKind {
  const e = ext(f)
  if (f.kind === 'image') return 'image'
  if (e === 'pdf') return 'pdf'
  if (e === 'html' || e === 'htm') return 'html'
  if (e === 'md' || e === 'markdown') return 'markdown'
  if (f.kind === 'text' || TEXT_EXT.has(e)) return 'text'
  // 视频/音频/Office/未知类型走兜底（用系统应用打开）
  return 'none'
}

// ── 聊天流里的文件卡片：点卡片进预览，右侧按钮 Show in Folder ──
export function FileCards({
  files,
  onOpen,
  activePath,
}: {
  files: PresentedFile[]
  onOpen: (f: PresentedFile) => void
  activePath?: string
}) {
  const { t } = useI18n()
  const valid = files.filter((f) => !f.error)
  if (valid.length === 0) return null
  return (
    <div className="my-1.5 flex flex-col gap-2">
      {valid.map((f) => {
        const Icon = fileIcon(f)
        const sub = [typeText(f), fmtSize(f.size)].filter(Boolean).join(' · ')
        const active = f.path === activePath
        return (
          <div
            key={f.path}
            onClick={() => onOpen(f)}
            className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 cursor-pointer transition-colors ${
              active
                ? 'border-primary bg-primary/[0.07]'
                : 'border-line hover:border-separator hover:bg-ink/[0.03]'
            }`}
          >
            <span className="grid place-items-center w-[38px] h-[38px] rounded-lg text-muted-foreground bg-ink/[0.06] shrink-0">
              <Icon size={19} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13.5px] font-medium truncate text-ink/90">
                {f.name || f.path}
              </span>
              <span className="block text-[11.5px] text-muted-foreground mt-0.5">{sub}</span>
            </span>
            <button
              onClick={(e) => {
                e.stopPropagation()
                void window.lumi.revealInFolder?.(f.path)
              }}
              className="shrink-0 inline-flex items-center gap-1.5 rounded-lg border border-line bg-canvas px-3 py-1.5 text-[13px] font-medium hover:border-separator hover:bg-ink/5 transition-colors"
            >
              <Folder size={15} className="text-muted-foreground" />
              {t('files.showInFolder')}
            </button>
          </div>
        )
      })}
    </div>
  )
}

// ── 右侧停靠预览面板 ──
export function PreviewPanel({ file, onClose }: { file: PresentedFile; onClose: () => void }) {
  const { t } = useI18n()
  const Icon = fileIcon(file)
  // 存在性只在打开预览时探测一次（卡片渲染不探）：null=检查中，true/false=结果。
  // nonce 递增 = 「重新检查」重新探测（文件可能又回来了）。
  const [exists, setExists] = useState<boolean | null>(null)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    let alive = true
    setExists(null)
    Promise.resolve(window.lumi.pathExists?.(file.path) ?? true)
      .then((ok) => alive && setExists(!!ok))
      .catch(() => alive && setExists(false))
    return () => {
      alive = false
    }
  }, [file.path, nonce])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const act =
    'grid place-items-center w-[30px] h-[30px] rounded-md text-muted-foreground hover:bg-ink/10 hover:text-ink transition-colors'
  return (
    <aside className="flex flex-col h-full min-w-0 border-l border-line bg-canvas">
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-line shrink-0">
        <Icon size={16} className="text-muted-foreground shrink-0 mr-1" />
        <span className="flex-1 min-w-0 truncate text-[13.5px] font-semibold">
          {file.name || file.path}
        </span>
        <button className={act} title={t('files.copyPath')} onClick={() => void navigator.clipboard.writeText(file.path)}>
          <Copy size={15} />
        </button>
        <button className={act} title={t('files.showInFolder')} onClick={() => void window.lumi.revealInFolder?.(file.path)}>
          <Folder size={15} />
        </button>
        <button className={act} title={t('files.openExternal')} onClick={() => void window.lumi.openPath?.(file.path)}>
          <ExternalLink size={15} />
        </button>
        <span className="w-px h-[18px] bg-line mx-0.5" />
        <button className={act} title={t('common.close')} onClick={onClose}>
          <X size={15} />
        </button>
      </div>
      <div className="flex-1 overflow-auto min-h-0">
        {exists === null ? null : exists ? (
          <PreviewBody file={file} />
        ) : (
          <MissingState file={file} onRecheck={() => setNonce((n) => n + 1)} />
        )}
      </div>
    </aside>
  )
}

// 文件已不在原位置（移动/改名/删除）：克制提示 + 最后已知路径 + 重新检查
function MissingState({ file, onRecheck }: { file: PresentedFile; onRecheck: () => void }) {
  const { t } = useI18n()
  return (
    <div className="h-full grid place-content-center justify-items-center text-center gap-1.5 px-7">
      <span className="grid place-items-center w-14 h-14 rounded-2xl bg-ink/[0.06] text-muted-foreground mb-2">
        <FileX2 size={27} />
      </span>
      <div className="text-[14.5px] font-semibold text-ink">{t('files.missingTitle')}</div>
      <div className="text-[12.5px] text-muted-foreground leading-relaxed">{t('files.missingDesc')}</div>
      <div className="mt-2 max-w-full truncate font-mono text-[11.5px] text-muted-foreground bg-panel border border-line rounded-lg px-2.5 py-1.5">
        {file.path}
      </div>
      <button
        onClick={onRecheck}
        className="mt-3.5 inline-flex items-center gap-2 rounded-lg border border-line px-4 py-1.5 text-[13px] font-medium hover:border-separator hover:bg-ink/5 transition-colors"
      >
        <RefreshCw size={15} className="text-muted-foreground" />
        {t('files.recheck')}
      </button>
    </div>
  )
}

function PreviewBody({ file }: { file: PresentedFile }) {
  const { t } = useI18n()
  const kind = previewKind(file)
  const url = fileUrl(file.path)
  // 文件过大：不内嵌（避免整块读进内存），提示 + 用系统应用打开
  if (file.size != null && file.size > MAX_PREVIEW_BYTES)
    return <NoPreview file={file} message={t('files.tooLarge', { size: fmtSize(file.size) })} />
  if (kind === 'image')
    return (
      <div className="p-6 grid place-items-center">
        <img src={url} alt={file.name} className="max-w-full rounded-lg border border-line" />
      </div>
    )
  if (kind === 'pdf' || kind === 'html')
    return (
      <iframe
        src={url}
        title={file.name || file.path}
        className="w-full h-full border-0"
        // HTML 用 allow-scripts（不带 allow-same-origin）：脚本可运行让交互页正常，
        // 但 iframe 是 opaque origin，对 lumi-file 的 fetch 跨域被拦，读不到本地文件→不能外传。
        sandbox={kind === 'html' ? 'allow-scripts' : undefined}
      />
    )
  if (kind === 'markdown' || kind === 'text') return <TextPreview url={url} markdown={kind === 'markdown'} />
  // none：无法内嵌（视频/音频/Office/未知类型）→ 兜底用系统应用打开
  return <NoPreview file={file} message={t('files.noPreview')} />
}

// 无法内嵌预览的统一兜底：图标 + 原因文案 + 用系统应用打开（过大/媒体/Office/未知类型复用）
function NoPreview({ file, message }: { file: PresentedFile; message: string }) {
  const { t } = useI18n()
  return (
    <div className="h-full grid place-content-center justify-items-center text-center text-muted-foreground text-sm gap-1 px-6">
      <span className="grid place-items-center w-14 h-14 rounded-xl bg-ink/[0.06] mb-2">
        <File size={26} />
      </span>
      <div>{message}</div>
      <button
        onClick={() => void window.lumi.openPath?.(file.path)}
        className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-[13px] font-semibold"
      >
        <ExternalLink size={15} />
        {t('files.openExternal')}
      </button>
    </div>
  )
}

function TextPreview({ url, markdown }: { url: string; markdown: boolean }) {
  const { t } = useI18n()
  const [text, setText] = useState<string | null>(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    let alive = true
    setText(null)
    setErr(false)
    fetch(url)
      .then((r) => r.text())
      .then((s) => alive && setText(s.slice(0, 500_000)))
      .catch(() => alive && setErr(true))
    return () => {
      alive = false
    }
  }, [url])
  if (err) return <div className="p-6 text-sm text-error/80">{t('files.loadFailed')}</div>
  if (text == null) return <div className="p-6 text-sm text-muted-foreground">…</div>
  if (markdown)
    return (
      <div className="md p-6 max-w-3xl mx-auto">
        <Markdown>{text}</Markdown>
      </div>
    )
  return (
    <pre className="p-5 text-xs leading-relaxed font-mono whitespace-pre overflow-auto selectable">{text}</pre>
  )
}

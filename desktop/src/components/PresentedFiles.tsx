import { useEffect, useRef, useState } from 'react'
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
import type { Gateway } from '../gateway'
import type { PresentedFile } from '../types'
import { useI18n } from '../i18n'
import { fmtSize, WIN_PATH } from '@/lib/utils'
import { ProgressBar } from './SettingsKit'
import { useEnvInstall } from './useEnvInstall'

// lumi-file://local/<abs path>：固定 host=local（自定义 standard scheme 不允许空 host，
// 否则 Chromium 会把路径首段当 host 吃掉）；各路径段 encodeURIComponent，空格/中文/#/? 都安全。
// 主进程 protocol.handle('lumi-file') 解码 pathname 后读本地文件。
// Windows 路径先转成 URL 能表达的形状：`C:\a\b` → `/C:/a/b`，UNC `\\srv\s` → `//srv/s`
// （POSIX 不动——反斜杠在那边是合法文件名字符）。主进程负责把 `/C:/…` 的前导斜杠去掉。
export const fileUrl = (p: string) => {
  const abs = WIN_PATH.test(p) ? p.replace(/\\/g, '/') : p
  const rooted = abs.startsWith('/') ? abs : '/' + abs
  return 'lumi-file://local' + rooted.split('/').map(encodeURIComponent).join('/')
}

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

// 内容取用通道的唯一决策点：本地走 lumi-file 零拷贝，远程经该机器 serve 的 /file 端点流回
const contentUrl = (path: string, remote?: boolean, gw?: Gateway) =>
  remote && gw ? gw.fileHttpUrl(path) : fileUrl(path)

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

const typeText = (f: PresentedFile) => (ext(f) || f.kind || 'file').toUpperCase()

// 文本类扩展名：这些走 fetch().text() 在面板里直接展示
const TEXT_EXT = new Set(
  'txt csv tsv json jsonl log xml yaml yml ini toml env py ts tsx js jsx mjs cjs css scss sass sh bash zsh rs go java kt c h cpp hpp cc rb php swift sql r lua pl conf cfg gitignore dockerfile makefile'.split(' '),
)

// 超过此大小不内嵌预览（用元数据 size 判定，不读文件）→ 显示提示 + 用系统应用打开
const MAX_PREVIEW_BYTES = 50 * 1024 * 1024

// 经后端 officecli 转 HTML 内嵌预览的 Office 格式（旧版 .doc/.xls/.ppt 不支持，走兜底）
const OFFICE_EXT = new Set(['docx', 'xlsx', 'pptx'])

type PreviewKind = 'image' | 'pdf' | 'html' | 'markdown' | 'text' | 'office' | 'none'
function previewKind(f: PresentedFile): PreviewKind {
  const e = ext(f)
  if (f.kind === 'image') return 'image'
  if (e === 'pdf') return 'pdf'
  if (e === 'html' || e === 'htm') return 'html'
  if (e === 'md' || e === 'markdown') return 'markdown'
  if (f.kind === 'text' || TEXT_EXT.has(e)) return 'text'
  if (OFFICE_EXT.has(e)) return 'office'
  // 视频/音频/旧版 Office/未知类型走兜底（用系统应用打开）
  return 'none'
}

// ── 聊天流里的文件卡片：点卡片进预览，右侧按钮 Show in Folder ──
// remote：文件在远程后端的盘上，本地「在访达中显示」无意义，隐藏
export function FileCards({
  files,
  onOpen,
  activePath,
  remote,
}: {
  files: PresentedFile[]
  onOpen: (f: PresentedFile) => void
  activePath?: string
  remote?: boolean
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
            {!remote && (
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
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── 右侧停靠预览面板 ──
// gw：当前会话所在机器的连接，Office 转换 RPC / 就地安装 / 远程文件 HTTP 通道走它；
// remote：文件在远程后端，内容经 gw.fileHttpUrl 流回，本地打开/访达按钮隐藏
export function PreviewPanel({
  file,
  gw,
  remote,
  onClose,
}: {
  file: PresentedFile
  gw?: Gateway
  remote?: boolean
  onClose: () => void
}) {
  const { t } = useI18n()
  const Icon = fileIcon(file)
  // 存在性只在打开预览时探测一次（卡片渲染不探）：null=检查中，true/false=结果。
  // nonce 递增 = 「重新检查」重新探测（文件可能又回来了）。远程文件用 HEAD 探测
  // （/file 端点原生支持），本地走主进程 IPC。
  const [exists, setExists] = useState<boolean | null>(null)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    let alive = true
    setExists(null)
    // 远程按状态码判：只有 404 = 不存在（413 超限等文件仍在，交给 PreviewBody 的
    // 分支给出正确文案）；无该机连接时不能拿本地盘探测冒充，视为存在、由 body 报错
    const probe = remote
      ? gw
        ? fetch(gw.fileHttpUrl(file.path), { method: 'HEAD' }).then((r) => r.status !== 404)
        : Promise.resolve(true)
      : Promise.resolve(window.lumi.pathExists?.(file.path) ?? true)
    probe
      .then((ok) => alive && setExists(!!ok))
      .catch(() => alive && setExists(false))
    return () => {
      alive = false
    }
  }, [file.path, nonce, remote, gw])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const act =
    'grid place-items-center w-[30px] h-[30px] rounded-md text-muted-foreground hover:bg-ink/10 hover:text-ink transition-colors'
  // 浮框整板（.demos/preview-float-panel.html 方案 A 定稿）：与右栏模块卡同族的玻璃浮卡,
  // 头部融在玻璃里不画分割线,内容区内嵌小圆角实底
  return (
    <aside className="sidebar-float rounded-card flex flex-col h-full min-w-0 overflow-hidden">
      <div className="flex items-center gap-1.5 px-3 py-2 shrink-0">
        <Icon size={16} className="text-muted-foreground shrink-0 mr-1" />
        <span className="flex-1 min-w-0 truncate text-[13.5px] font-semibold">
          {file.name || file.path}
        </span>
        <button className={act} title={t('files.copyPath')} onClick={() => void navigator.clipboard.writeText(file.path)}>
          <Copy size={15} />
        </button>
        {!remote && (
          <>
            <button className={act} title={t('files.showInFolder')} onClick={() => void window.lumi.revealInFolder?.(file.path)}>
              <Folder size={15} />
            </button>
            <button className={act} title={t('files.openExternal')} onClick={() => void window.lumi.openPath?.(file.path)}>
              <ExternalLink size={15} />
            </button>
          </>
        )}
        <span className="w-px h-[18px] bg-line mx-0.5" />
        <button className={act} title={t('common.close')} onClick={onClose}>
          <X size={15} />
        </button>
      </div>
      <div className="flex-1 overflow-auto min-h-0 mx-2.5 mb-2.5 rounded-[10px] bg-canvas">
        {exists === null ? null : exists ? (
          <PreviewBody file={file} gw={gw} remote={remote} />
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

function PreviewBody({ file, gw, remote }: { file: PresentedFile; gw?: Gateway; remote?: boolean }) {
  const { t } = useI18n()
  const kind = previewKind(file)
  // 远程但无该机连接：宁可报加载失败，也不落回 lumi-file 读本地盘——同路径文件
  // 存在于两台机器时会静默显示错误机器的内容（同 projectHomeApi 不回退的理由）
  if (remote && !gw) return <NoPreview file={file} remote message={t('files.loadFailed')} />
  const url = contentUrl(file.path, remote, gw)
  // 文件过大：不内嵌（避免整块读进内存），提示 + 用系统应用打开
  if (file.size != null && file.size > MAX_PREVIEW_BYTES)
    return <NoPreview file={file} remote={remote} message={t('files.tooLarge', { size: fmtSize(file.size) })} />
  if (kind === 'office') return <OfficePreview file={file} gw={gw} remote={remote} />
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
  // none：无法内嵌（视频/音频/旧版 Office/未知类型）→ 兜底用系统应用打开
  return <NoPreview file={file} remote={remote} message={t('files.noPreview')} />
}

// Office 文档（docx/xlsx/pptx）：经后端 officecli 转自包含 HTML 后走 iframe。
// 未装转换组件时就地引导安装（env_install target=officecli，机器级一次性）。
function OfficePreview({ file, gw, remote }: { file: PresentedFile; gw?: Gateway; remote?: boolean }) {
  const { t } = useI18n()
  const [result, setResult] = useState<
    | { status: 'loading' }
    | { status: 'ready'; htmlPath: string }
    | { status: 'missing' }
    | { status: 'error'; detail?: string }
  >({ status: 'loading' })
  const [nonce, setNonce] = useState(0)
  const resultRef = useRef(result)
  resultRef.current = result
  const { progress, install } = useEnvInstall(gw, {
    targets: ['officecli'],
    // 安装结束（成败皆广播 env.state，含一键装齐的 target='all'）重试渲染；已 ready
    // 的预览不重发 RPC。失败时后端仍报 missing，回到安装引导
    onState: () => {
      if (resultRef.current.status !== 'ready') setNonce((n) => n + 1)
    },
  })

  useEffect(() => {
    if (!gw) {
      setResult({ status: 'error' })
      return
    }
    let alive = true
    setResult({ status: 'loading' })
    gw.renderOffice(file.path)
      .then((r) => {
        if (!alive) return
        if (r.ok && r.html_path) setResult({ status: 'ready', htmlPath: r.html_path })
        else if (r.reason === 'missing') setResult({ status: 'missing' })
        // 转换失败原因（officecli stderr 开头的人话段 / RPC 错误文本）原样透出：
        // 「缺 libicu」这类环境问题用户能看懂并自行处置，笼统文案只会逼人猜
        else setResult({ status: 'error', detail: r.message })
      })
      .catch((e) => alive && setResult({ status: 'error', detail: e?.message }))
    return () => {
      alive = false
    }
  }, [gw, file.path, nonce])

  if (result.status === 'ready')
    return (
      <iframe
        // 渲染产物生成在会话所在机器，取用通道同源文件（contentUrl）
        src={contentUrl(result.htmlPath, remote, gw)}
        title={file.name || file.path}
        className="w-full h-full border-0"
        // 同 html 预览：脚本可跑（渲染引擎内联 JS），opaque origin 拦跨域读本地文件
        sandbox="allow-scripts"
      />
    )
  if (result.status === 'error')
    return <NoPreview file={file} remote={remote} message={t('files.renderFailed')} detail={result.detail} />
  if (result.status === 'missing') {
    const installing = progress['officecli']
    return (
      <NoPreview
        file={file}
        remote={remote}
        icon={FileText}
        message={t('files.officeMissing')}
        action={
          installing ? (
            <ProgressBar progress={installing} className="mt-3" />
          ) : (
            <button
              onClick={() => install('officecli')}
              className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-[13px] font-semibold"
            >
              {t('files.installPreview')}
            </button>
          )
        }
      />
    )
  }
  // loading：转换通常亚秒级，静默留白避免闪烁
  return null
}

// 无法内嵌预览的统一兜底骨架：图标 + 原因文案 + 用系统应用打开（过大/媒体/Office/
// 未知类型/未装组件复用）。remote：文件不在本机，本地打开入口隐藏。
// detail：具体失败原因（如 officecli 的「缺 libicu」stderr），mono 小字可选中复制。
// action：主操作插槽（如安装按钮/进度条）；给了它，「用系统应用打开」降为次级文字入口
function NoPreview({
  file,
  remote,
  message,
  detail,
  icon: Icon = File,
  action,
}: {
  file: PresentedFile
  remote?: boolean
  message: string
  detail?: string
  icon?: LucideIcon
  action?: React.ReactNode
}) {
  const { t } = useI18n()
  const openExternal = () => void window.lumi.openPath?.(file.path)
  return (
    <div className="h-full grid place-content-center justify-items-center text-center text-muted-foreground text-sm gap-1 px-6">
      <span className="grid place-items-center w-14 h-14 rounded-xl bg-ink/[0.06] mb-2">
        <Icon size={26} />
      </span>
      <div>{message}</div>
      {detail && (
        <div className="mt-2 max-w-md max-h-32 overflow-auto text-left font-mono text-[11px] leading-relaxed text-muted-foreground bg-panel border border-line rounded-lg px-3 py-2 whitespace-pre-wrap break-all selectable">
          {detail}
        </div>
      )}
      {action}
      {!remote &&
        (action ? (
          <button
            onClick={openExternal}
            className="mt-1.5 text-[12.5px] text-muted-foreground underline-offset-2 hover:underline"
          >
            {t('files.openExternal')}
          </button>
        ) : (
          <button
            onClick={openExternal}
            className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-[13px] font-semibold"
          >
            <ExternalLink size={15} />
            {t('files.openExternal')}
          </button>
        ))}
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
      .then((r) => {
        // 远程 /file 通道的非 2xx（401/404/413）带空体：不查状态码会渲染成静默空白
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.text()
      })
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

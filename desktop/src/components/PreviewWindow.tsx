import { Copy, ExternalLink, Folder, Maximize2, Minimize2, MoreHorizontal, X } from 'lucide-react'
import {
  memo, useEffect, useLayoutEffect, useRef, useState,
  type JSX, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent,
} from 'react'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu.tsx'
import { basename } from '@/lib/utils.ts'
import type { Gateway } from '../gateway.ts'
import { useI18n } from '../i18n.ts'
import type { Artifact } from '../types.ts'
import { fileIcon, PreviewContent } from './Artifacts.tsx'

type PreviewWindowProps = {
  files: Artifact[]
  activePath: string | null
  expanded: boolean
  nativeTitleBar: boolean
  gw?: Gateway
  remote: boolean
  onSelect: (path: string) => void
  onCloseTab: (path: string) => void
  onClose: () => void
  onToggleExpanded: () => void
}

function previewName(file: Artifact): string {
  return file.name || basename(file.path)
}

export const PreviewWindow = memo(function PreviewWindow({
  files, activePath, expanded, nativeTitleBar, gw, remote,
  onSelect, onCloseTab, onClose, onToggleExpanded,
}: PreviewWindowProps): JSX.Element {
  const { t } = useI18n()
  const [menuOpen, setMenuOpen] = useState(false)
  const [size, setSize] = useState({ width: 420, height: 296 })
  const viewportRef = useRef<HTMLDivElement>(null)
  const tabsRef = useRef<HTMLDivElement>(null)
  const activeFile = files.find(file => file.path === activePath) ?? files[0]
  const expandLabel = t(expanded ? 'files.shrink' : 'files.expand')
  const ExpandIcon = expanded ? Minimize2 : Maximize2

  useLayoutEffect(() => {
    const viewport = viewportRef.current!
    function measure(): void {
      const { clientWidth: width, clientHeight: height } = viewport
      // 隐藏的右栏保留上次尺寸，避免 iframe 被缩到 0 而丢失模型状态。
      if (!width || !height) return
      setSize(previous => {
        if (previous.width === width && previous.height === height) return previous
        return { width, height }
      })
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(viewport)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    tabsRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activePath])

  useEffect(() => {
    if (!expanded) return
    function onKey(event: KeyboardEvent): void {
      if (event.key === 'Escape' && !event.defaultPrevented && !menuOpen) onToggleExpanded()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [expanded, menuOpen, onToggleExpanded])

  function handleHeaderDoubleClick(event: MouseEvent<HTMLElement>): void {
    if (!(event.target as HTMLElement).closest('button')) onToggleExpanded()
  }

  function handleTabKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, index: number): void {
    let next: number
    switch (event.key) {
      case 'ArrowRight':
        next = (index + 1) % files.length
        break
      case 'ArrowLeft':
        next = (index + files.length - 1) % files.length
        break
      case 'Home':
        next = 0
        break
      case 'End':
        next = files.length - 1
        break
      case 'Delete':
        event.preventDefault()
        onCloseTab(files[index].path)
        return
      default:
        return
    }
    event.preventDefault()
    onSelect(files[next].path)
    tabsRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus()
  }

  const scale = expanded ? 1 : Math.min(1, size.width / 980)
  return (
    <section className={`preview-window ${expanded ? 'preview-window-expanded' : 'sidebar-float rounded-card'}`} aria-label={t('files.previewWindow')}>
      <header
        className="preview-window-header"
        style={expanded && nativeTitleBar ? { paddingLeft: 100 } : undefined}
        onDoubleClick={handleHeaderDoubleClick}
      >
        <div ref={tabsRef} className="preview-tabs titlebar-interactive" role="tablist" aria-label={t('files.previewTabs')}>
          {files.map((file, index) => {
            const selected = file.path === activePath
            const Icon = fileIcon(file)
            const closeLabel = t('files.closeTab', { name: previewName(file) })
            return (
              <div key={file.path} className={`preview-tab ${selected ? 'preview-tab-selected' : ''}`}>
                <button
                  role="tab"
                  aria-selected={selected}
                  tabIndex={selected ? 0 : -1}
                  title={file.path}
                  onClick={() => onSelect(file.path)}
                  onKeyDown={event => handleTabKeyDown(event, index)}
                >
                  <Icon size={13} /><span>{previewName(file)}</span>
                </button>
                <button
                  className="preview-tab-close"
                  tabIndex={-1}
                  title={closeLabel}
                  aria-label={closeLabel}
                  onClick={() => onCloseTab(file.path)}
                >
                  <X size={11} />
                </button>
              </div>
            )
          })}
        </div>
        <div className="preview-window-drag app-drag" />
        <div className="preview-window-actions titlebar-interactive">
          <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
            <DropdownMenuTrigger asChild>
              <button title={t('files.actions')} aria-label={t('files.actions')}><MoreHorizontal size={15} /></button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-auto min-w-[178px]" onEscapeKeyDown={event => event.stopPropagation()}>
              <DropdownMenuItem onSelect={() => void navigator.clipboard.writeText(activeFile.path)}><Copy size={14} />{t('files.copyPath')}</DropdownMenuItem>
              {!remote && <>
                <DropdownMenuItem onSelect={() => void window.lumi.revealInFolder?.(activeFile.path)}><Folder size={14} />{t('files.showInFolder')}</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void window.lumi.openPath?.(activeFile.path)}><ExternalLink size={14} />{t('files.openExternal')}</DropdownMenuItem>
              </>}
            </DropdownMenuContent>
          </DropdownMenu>
          <span className="preview-window-divider" />
          <button title={expandLabel} aria-label={expandLabel} onClick={onToggleExpanded}><ExpandIcon size={15} /></button>
          <button title={t('files.closeWindow')} aria-label={t('files.closeWindow')} onClick={onClose}><X size={15} /></button>
        </div>
      </header>
      <div ref={viewportRef} className="preview-viewport">
        {files.map(file => {
          const selected = file.path === activePath
          return (
            <div
              key={file.path}
              className="preview-document"
              role="tabpanel"
              aria-label={previewName(file)}
              aria-hidden={!selected}
              inert={!selected}
              style={{
                width: expanded ? size.width : 980,
                height: size.height / scale,
                transform: `scale(${scale})`,
                visibility: selected ? 'visible' : 'hidden',
              }}
            >
              <PreviewContent file={file} gw={gw} remote={remote} />
            </div>
          )
        })}
        {!expanded && <button className="preview-enlarge-hit" aria-label={t('files.expand')} onClick={onToggleExpanded}><span><Maximize2 size={12} />{t('files.clickToExpand')}</span></button>}
      </div>
    </section>
  )
})

import { memo, useEffect, useState, type ReactNode } from 'react'
import { Minus, Minimize2, Square, X } from 'lucide-react'
import { LANGS, useI18n, type Lang } from '../i18n'
import appIcon from '../../assets/icon.png'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

type Props = {
  onNewChat: () => void
  onOpenSettings: () => void
}

// 视图菜单里的命令项——命令名 + 文案 key + 展示的快捷键，逐条 map 渲染。
const VIEW_COMMANDS = [
  { cmd: 'reload', label: 'titlebar.reload', shortcut: 'Ctrl+R' },
  { cmd: 'reset-zoom', label: 'titlebar.resetZoom', shortcut: 'Ctrl+0' },
  { cmd: 'zoom-in', label: 'titlebar.zoomIn', shortcut: 'Ctrl++' },
  { cmd: 'zoom-out', label: 'titlebar.zoomOut', shortcut: 'Ctrl+-' },
] as const

export const AppTitleBar = memo(function AppTitleBar({ onNewChat, onOpenSettings }: Props) {
  const { t, lang, setLang } = useI18n()
  const [maximized, setMaximized] = useState(false)
  // Windows 三键走系统原生 overlay（WCO，见 main.cjs），自绘的只给 Linux；
  // 自绘按钮依赖 no-drag 命中矩形，缩放/DPI 变化后会失效成「点不动」
  const nativeControls = window.lumi.platform === 'win32'

  useEffect(() => {
    if (nativeControls) return
    void window.lumi.windowControls?.isMaximized().then(setMaximized)
    return window.lumi.windowControls?.onMaximizedChange(setMaximized)
  }, [nativeControls])

  const run = (command: string) => {
    void window.lumi.menuCommand?.(command)
  }

  return (
    <div
      className="titlebar-native-font app-drag shrink-0 flex items-center border-b border-line/30 bg-canvas select-none"
      // Windows WCO 下原生三键恒为 32 DIP，而 CSS px 随页面缩放漂移——
      // 用 env 取 overlay 实际高度（Chromium 已按缩放换算），任何缩放下都与原生按钮齐平；
      // Linux 无 WCO 时 env 未定义，回退 32px 即原 h-8
      style={{ height: 'env(titlebar-area-height, 32px)' }}
    >
      <div className="flex h-full items-center gap-2 pl-2">
        <div className="flex items-center gap-2 pr-1">
          <img src={appIcon} alt="" className="size-4 rounded-sm" />
          <span className="font-normal text-ink">Lumi</span>
        </div>
        <MenuButton label={t('titlebar.file')}>
          <DropdownMenuItem onClick={onNewChat}>
            {t('titlebar.newChat')}
            <DropdownMenuShortcut>Ctrl+N</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={onOpenSettings}>
            {t('menu.settings')}
            <DropdownMenuShortcut>Ctrl+,</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => void window.lumi.windowControls?.close()}>
            {t('titlebar.closeWindow')}
            <DropdownMenuShortcut>Alt+F4</DropdownMenuShortcut>
          </DropdownMenuItem>
        </MenuButton>
        <MenuButton label={t('titlebar.view')}>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>{t('menu.language')}</DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="titlebar-native-menu no-drag w-36">
              <DropdownMenuRadioGroup value={lang} onValueChange={(value) => setLang(value as Lang)}>
                {LANGS.map((item) => (
                  <DropdownMenuRadioItem key={item.code} value={item.code}>
                    {item.label}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuSeparator />
          {VIEW_COMMANDS.map((item) => (
            <DropdownMenuItem key={item.cmd} onClick={() => run(item.cmd)}>
              {t(item.label)}
              <DropdownMenuShortcut>{item.shortcut}</DropdownMenuShortcut>
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => run('toggle-devtools')}>
            {t('titlebar.devtools')}
          </DropdownMenuItem>
        </MenuButton>
        <MenuButton label={t('titlebar.help')}>
          <DropdownMenuItem onClick={() => run('open-repo')}>
            {t('titlebar.openRepo')}
          </DropdownMenuItem>
        </MenuButton>
      </div>

      <div className="flex-1" />

      {!nativeControls && (
        <div className="no-drag flex h-full">
          <button
            type="button"
            title={t('titlebar.minimize')}
            className="grid h-8 w-11 place-items-center text-muted-foreground transition-colors hover:bg-ink/10 hover:text-ink"
            onClick={() => void window.lumi.windowControls?.minimize()}
          >
            <Minus size={15} />
          </button>
          <button
            type="button"
            title={maximized ? t('titlebar.restore') : t('titlebar.maximize')}
            className="grid h-8 w-11 place-items-center text-muted-foreground transition-colors hover:bg-ink/10 hover:text-ink"
            onClick={() => void window.lumi.windowControls?.toggleMaximize()}
          >
            {maximized ? <Minimize2 size={14} /> : <Square size={13} />}
          </button>
          <button
            type="button"
            title={t('common.close')}
            className="grid h-8 w-11 place-items-center text-muted-foreground transition-colors hover:bg-error hover:text-white"
            onClick={() => void window.lumi.windowControls?.close()}
          >
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  )
})

function MenuButton({ label, children }: { label: string; children: ReactNode }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="no-drag h-7 rounded-md px-2 text-muted-foreground transition-colors hover:bg-ink/8 hover:text-ink data-[state=open]:bg-ink/10 data-[state=open]:text-ink"
        >
          {label}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side="bottom"
        align="start"
        sideOffset={6}
        className="titlebar-native-menu no-drag w-52"
      >
        {children}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

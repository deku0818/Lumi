import { useEffect, useState, type ReactNode } from 'react'
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

export function AppTitleBar({ onNewChat, onOpenSettings }: Props) {
  const { t, lang, setLang } = useI18n()
  const [maximized, setMaximized] = useState(false)

  useEffect(() => {
    void window.lumi.windowControls?.isMaximized().then(setMaximized)
    return window.lumi.windowControls?.onMaximizedChange(setMaximized)
  }, [])

  const run = (command: string) => {
    void window.lumi.menuCommand?.(command)
  }

  return (
    <div
      className="titlebar-native-font app-drag h-8 shrink-0 flex items-center border-b border-line/30 bg-canvas select-none"
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
        <MenuButton label={t('titlebar.edit')}>
          <DropdownMenuItem onClick={() => run('undo')}>
            {t('titlebar.undo')}
            <DropdownMenuShortcut>Ctrl+Z</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => run('redo')}>
            {t('titlebar.redo')}
            <DropdownMenuShortcut>Ctrl+Y</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => run('cut')}>
            {t('titlebar.cut')}
            <DropdownMenuShortcut>Ctrl+X</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => run('copy')}>
            {t('common.copy')}
            <DropdownMenuShortcut>Ctrl+C</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => run('paste')}>
            {t('titlebar.paste')}
            <DropdownMenuShortcut>Ctrl+V</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => run('select-all')}>
            {t('titlebar.selectAll')}
            <DropdownMenuShortcut>Ctrl+A</DropdownMenuShortcut>
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
          <DropdownMenuItem onClick={() => run('reload')}>
            {t('titlebar.reload')}
            <DropdownMenuShortcut>Ctrl+R</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => run('reset-zoom')}>
            {t('titlebar.resetZoom')}
            <DropdownMenuShortcut>Ctrl+0</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => run('zoom-in')}>
            {t('titlebar.zoomIn')}
            <DropdownMenuShortcut>Ctrl++</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => run('zoom-out')}>
            {t('titlebar.zoomOut')}
            <DropdownMenuShortcut>Ctrl+-</DropdownMenuShortcut>
          </DropdownMenuItem>
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
          onClick={() => void window.lumi.windowControls?.toggleMaximize().then(setMaximized)}
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
    </div>
  )
}

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

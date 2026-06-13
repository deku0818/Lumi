import { useState } from 'react'
import { Folder, FolderPlus, Plus, X } from 'lucide-react'
import { useI18n } from '../i18n'
import { basename } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
} from '@/components/ui/dropdown-menu'

// composer 的「添加文件夹」（交互定稿见 .demos/lumi-projects.html）：
// 图标右上角挂数量徽标；无目录时点击直接弹原生选择器（少一步），
// 有目录时点击弹增减菜单——头部「＋」追加，目录行 hover 出 × 移除。
export function FolderMenu({
  folders,
  onAdd,
  onRemove,
}: {
  folders: string[]
  onAdd: () => void
  onRemove: (path: string) => void
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  return (
    <DropdownMenu
      open={open}
      onOpenChange={(o) => {
        if (o && folders.length === 0) {
          onAdd()
          return
        }
        setOpen(o)
      }}
    >
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('folders.add')}
          title={open ? undefined : t('folders.add')}
          className="relative text-muted-foreground"
        >
          <FolderPlus />
          {folders.length > 0 && (
            <span className="absolute top-0 right-0 min-w-3.5 h-3.5 rounded-full bg-primary px-1 text-[9.5px] font-bold leading-[14px] text-primary-foreground text-center">
              {folders.length}
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-64">
        <div className="flex items-center gap-1 pl-2 pr-0.5 py-0.5">
          <span className="flex-1 text-[11px] text-muted-foreground select-none">
            {t('folders.title')}
          </span>
          <button
            onClick={onAdd}
            aria-label={t('folders.add')}
            className="size-6 grid place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-ink transition outline-none"
          >
            <Plus size={14} />
          </button>
        </div>
        {folders.map((f) => (
          <div
            key={f}
            title={f}
            className="group flex items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
          >
            <Folder size={13} className="shrink-0 text-muted-foreground" />
            <span className="flex-1 min-w-0 truncate">{basename(f)}</span>
            <button
              onClick={() => onRemove(f)}
              aria-label={t('projects.remove')}
              className="size-5 grid place-items-center rounded text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-ink transition outline-none"
            >
              <X size={12} />
            </button>
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

import { useState } from 'react'
import { Folder } from 'lucide-react'
import { useI18n } from '../i18n'
import { basename } from '@/lib/utils'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

// 新建项目对话框（交互定稿 .demos/lumi-projects.html）：
// 原生选择器选目录 → 末端目录名自动填充为名称（可改）。
export function NewProjectDialog({
  onCreate,
  onCancel,
}: {
  onCreate: (path: string, name: string) => void
  onCancel: () => void
}) {
  const { t } = useI18n()
  const [path, setPath] = useState('')
  const [name, setName] = useState('')

  const pick = async () => {
    const dir = await window.lumi.pickDirectory?.()
    if (!dir) return
    setPath(dir)
    setName(basename(dir))
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('projects.new')}</DialogTitle>
          <DialogDescription>{t('projects.newSub')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs text-muted-foreground">
              {t('projects.chooseFolder')}
            </label>
            <button
              onClick={() => void pick()}
              className="flex w-full items-center gap-2.5 rounded-xl border border-line bg-canvas px-3 py-2.5 text-left text-[12.5px] transition-colors hover:border-separator"
            >
              <Folder size={14} className="shrink-0 text-muted-foreground" />
              {path ? (
                <span className="truncate font-mono">{path}</span>
              ) : (
                <span className="text-muted-foreground/70">{t('projects.pickFolder')}</span>
              )}
            </button>
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-muted-foreground">
              {t('projects.name')}
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('projects.namePlaceholder')}
              className="w-full rounded-xl border border-line bg-canvas px-3 py-2.5 text-sm outline-none transition-colors focus:border-primary/40"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          <Button disabled={!path || !name.trim()} onClick={() => onCreate(path, name.trim())}>
            {t('projects.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

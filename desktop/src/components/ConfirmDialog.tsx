// 通用二次确认弹窗：用于删除等不可逆操作。基于 shadcn Dialog。
import type { ReactNode } from 'react'
import { useI18n } from '../i18n'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

export function ConfirmDialog({
  title,
  message,
  icon,
  confirmLabel,
  variant = 'error',
  onConfirm,
  onCancel,
}: {
  title: ReactNode
  message: ReactNode
  icon?: ReactNode // 标题前的语义图标（如删除的红三角 / 切换的金三角）
  confirmLabel?: string
  // 默认红（删除类）；可逆但有代价的确认（切模型废缓存 / 换绑项目）用 default
  variant?: 'error' | 'default'
  onConfirm: () => void
  onCancel: () => void
}) {
  const { t } = useI18n()
  return (
    <Dialog open onOpenChange={(o) => !o && onCancel()}>
      <DialogContent showCloseButton={false} className="sm:max-w-sm p-5">
        <DialogHeader>
          <DialogTitle className={icon ? 'flex items-center gap-2' : undefined}>
            {icon}
            {title}
          </DialogTitle>
          <DialogDescription className="break-words leading-relaxed">{message}</DialogDescription>
        </DialogHeader>
        {/* 单一底色：抹掉 DialogFooter 默认的灰底 + 分隔线 + 负边距出血 */}
        <DialogFooter className="m-0 border-t-0 bg-transparent p-0 pt-1">
          <Button variant="ghost" className="bg-ink/10 hover:bg-ink/15" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          <Button variant={variant === 'error' ? 'destructive' : 'default'} onClick={onConfirm}>
            {confirmLabel ?? t('common.delete')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

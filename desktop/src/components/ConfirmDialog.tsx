// 通用二次确认弹窗：用于删除等不可逆操作。基于 shadcn Dialog。
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
  confirmLabel,
  variant = 'destructive',
  onConfirm,
  onCancel,
}: {
  title: string
  message: string
  confirmLabel?: string
  // 默认红（删除类）；可逆但有代价的确认（切模型废缓存）用 default
  variant?: 'destructive' | 'default'
  onConfirm: () => void
  onCancel: () => void
}) {
  const { t } = useI18n()
  return (
    <Dialog open onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="break-words">{message}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          <Button variant={variant} onClick={onConfirm}>
            {confirmLabel ?? t('common.delete')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

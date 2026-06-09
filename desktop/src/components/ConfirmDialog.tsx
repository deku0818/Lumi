// 通用二次确认弹窗：用于删除等不可逆操作。复用 ModalShell。
import { ModalShell } from './ModalShell'

export function ConfirmDialog({
  title,
  message,
  confirmLabel = '删除',
  onConfirm,
  onCancel,
}: {
  title: string
  message: string
  confirmLabel?: string
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <ModalShell maxWidth="max-w-sm">
      <h2 className="text-base font-semibold mb-2">{title}</h2>
      <p className="text-sm text-muted mb-5 break-words">{message}</p>
      <div className="flex gap-3 justify-end">
        <button
          onClick={onCancel}
          className="px-4 py-2 rounded-lg bg-panel border border-line hover:border-line/80 transition text-sm"
        >
          取消
        </button>
        <button
          onClick={onConfirm}
          className="px-4 py-2 rounded-lg bg-error text-canvas font-medium hover:brightness-110 transition text-sm"
        >
          {confirmLabel}
        </button>
      </div>
    </ModalShell>
  )
}

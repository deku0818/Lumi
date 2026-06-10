// 工具审批弹窗。MVP 只提供「允许本次 / 拒绝」两个纯 resume 决策；
// always_allow / accept_edits 等需服务端持久化的记忆选项留待后续。
import { useI18n } from '../i18n'
import { Button } from '@/components/ui/button'

interface ToolCall {
  name: string
  args: unknown
}

interface ApprovalData {
  tool_calls?: ToolCall[]
  warnings?: string[]
  boundary_violations?: string[]
}

export function ApprovalDialog({
  data,
  onDecide,
}: {
  data: ApprovalData
  onDecide: (decision: 'approve' | 'reject') => void
}) {
  const { t } = useI18n()
  const calls = data.tool_calls ?? []
  const warnings = data.warnings ?? []
  const boundary = data.boundary_violations ?? []

  return (
    <div className="border border-line rounded-2xl bg-surface/50 p-4">
      <h2 className="text-base font-semibold mb-4 flex items-center gap-2">
          <span className="text-primary">✦</span>
          {t('approval.title')}
        </h2>

        {calls.map((c, i) => (
          <div key={i} className="mb-3">
            <div className="font-mono text-sm text-primary">{c.name}</div>
            <pre className="text-xs bg-canvas border border-line rounded-lg p-2.5 mt-1.5 overflow-auto max-h-40 text-muted-foreground">
              {JSON.stringify(c.args, null, 2)}
            </pre>
          </div>
        ))}

        {boundary.length > 0 && (
          <div className="text-xs text-error mb-2">⚠ {t('approval.boundary')}{boundary.join('、')}</div>
        )}
        {warnings.map((w, i) => (
          <div key={i} className="text-xs text-primary/90 mb-1">
            {w}
          </div>
        ))}

        <div className="flex gap-2 mt-5 justify-end">
          <Button variant="outline" onClick={() => onDecide('reject')}>
            {t('approval.reject')}
          </Button>
          <Button onClick={() => onDecide('approve')}>{t('approval.allow')}</Button>
        </div>

        <p className="text-[11px] text-muted-foreground mt-4">{t('approval.memoryNote')}</p>
    </div>
  )
}

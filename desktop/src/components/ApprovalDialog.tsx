// 工具审批弹窗。MVP 只提供「允许本次 / 拒绝」两个纯 resume 决策；
// always_allow / accept_edits 等需服务端持久化的记忆选项留待后续。
import { ModalShell } from './ModalShell'

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
  const calls = data.tool_calls ?? []
  const warnings = data.warnings ?? []
  const boundary = data.boundary_violations ?? []

  return (
    <ModalShell maxWidth="max-w-lg">
      <h2 className="text-base font-semibold mb-4 flex items-center gap-2">
          <span className="text-accent">✦</span>
          需要你的许可
        </h2>

        {calls.map((c, i) => (
          <div key={i} className="mb-3">
            <div className="font-mono text-sm text-accent">{c.name}</div>
            <pre className="text-xs bg-canvas border border-line rounded-lg p-2.5 mt-1.5 overflow-auto max-h-40 text-muted">
              {JSON.stringify(c.args, null, 2)}
            </pre>
          </div>
        ))}

        {boundary.length > 0 && (
          <div className="text-xs text-error mb-2">⚠ 超出工作区：{boundary.join('、')}</div>
        )}
        {warnings.map((w, i) => (
          <div key={i} className="text-xs text-accent/90 mb-1">
            {w}
          </div>
        ))}

        <div className="flex gap-3 mt-5 justify-end">
          <button
            onClick={() => onDecide('reject')}
            className="px-4 py-2 rounded-lg bg-panel border border-line hover:border-separator transition"
          >
            拒绝
          </button>
          <button
            onClick={() => onDecide('approve')}
            className="px-4 py-2 rounded-lg bg-accent text-canvas font-medium hover:brightness-110 transition"
          >
            允许执行
          </button>
        </div>

        <p className="text-[11px] text-muted mt-4">
          「始终允许 / 本次会话自动编辑」等记忆选项将在后续版本支持。
        </p>
    </ModalShell>
  )
}

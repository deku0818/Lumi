// ExitPlanMode 交互：展示计划正文（由服务端读文件富化到 plan_content）+ 批准/拒绝。
// resume：批准发 'approved'，拒绝发 PLAN_REJECTED。
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ModalShell } from './ModalShell'

const PLAN_REJECTED = '__plan_rejected__'

interface PlanData {
  plan_file_path?: string
  plan_content?: string
}

export function PlanDialog({
  data,
  onApprove,
  onReject,
}: {
  data: PlanData
  onApprove: () => void
  onReject: () => void
}) {
  const name = (data.plan_file_path ?? '').split('/').pop()
  return (
    <ModalShell maxWidth="max-w-2xl" className="max-h-[85vh] flex flex-col">
      <h2 className="text-base font-semibold mb-2 flex items-center gap-2">
          <span>📋</span>
          计划审批
        </h2>
        {name && <div className="text-accent font-mono text-sm mb-3">● {name}</div>}

        <div className="md flex-1 overflow-auto border border-line rounded-lg p-3.5 bg-canvas/40">
          {data.plan_content ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.plan_content}</ReactMarkdown>
          ) : (
            <span className="text-muted">（无计划内容）</span>
          )}
        </div>

        <div className="flex gap-3 justify-end mt-5">
          <button
            onClick={onReject}
            className="px-4 py-2 rounded-lg bg-panel border border-line hover:border-error/50 transition"
          >
            拒绝 — 继续修改
          </button>
          <button
            onClick={onApprove}
            className="px-4 py-2 rounded-lg bg-success text-canvas font-medium hover:brightness-110 transition"
          >
            批准 — 开始实施
          </button>
        </div>
    </ModalShell>
  )
}

export { PLAN_REJECTED }

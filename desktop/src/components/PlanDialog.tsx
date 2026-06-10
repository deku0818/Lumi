// ExitPlanMode 交互：展示计划正文（由服务端读文件富化到 plan_content）+ 批准/拒绝。
// resume：批准发 'approved'，拒绝发 PLAN_REJECTED。
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useI18n } from '../i18n'
import { Button } from '@/components/ui/button'

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
  const { t } = useI18n()
  const name = (data.plan_file_path ?? '').split('/').pop()
  return (
    <div className="border border-line rounded-2xl bg-surface/50 p-4 max-h-[70vh] flex flex-col">
      <h2 className="text-base font-semibold mb-2 flex items-center gap-2">
          <span>📋</span>
          {t('plan.title')}
        </h2>
        {name && <div className="text-primary font-mono text-sm mb-3">● {name}</div>}

        <div className="md flex-1 overflow-auto border border-line rounded-lg p-3.5 bg-canvas/40">
          {data.plan_content ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.plan_content}</ReactMarkdown>
          ) : (
            <span className="text-muted">{t('plan.empty')}</span>
          )}
        </div>

        <div className="flex gap-2 justify-end mt-5">
          <Button variant="outline" onClick={onReject}>
            {t('plan.reject')}
          </Button>
          <Button onClick={onApprove} className="bg-success text-white hover:bg-success/90">
            {t('plan.approve')}
          </Button>
        </div>
    </div>
  )
}

export { PLAN_REJECTED }

import { Fragment } from 'react'
import { ChevronDown, Check, CircleHelp, SquarePen, Zap, ShieldCheck } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ToolMode } from '../types'
import { useI18n } from '../i18n'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'

const MODES: { id: ToolMode; icon: LucideIcon }[] = [
  { id: 'default', icon: CircleHelp },
  { id: 'accept_edits', icon: SquarePen },
  { id: 'privileged', icon: Zap },
  { id: 'auto', icon: ShieldCheck },
]

// 输入区工具审批模式切换器（变体 B：盾形图标芯片）。auto = AI 审批，菜单内
// 副标题显示当前分类器模型（classifierLabel）。交互定稿见 .demos/lumi-approval-mode.html。
export function ApprovalModePicker({
  value,
  onChange,
  classifierLabel,
}: {
  value: ToolMode
  onChange: (m: ToolMode) => void
  // 当前分类器模型名（未单独配时为空 = 跟随会话模型）
  classifierLabel?: string
}) {
  const { t } = useI18n()
  const isAuto = value === 'auto'

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          title={t('approval.label')}
          className={`group flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs transition outline-none border ${
            isAuto
              ? 'border-primary/45 text-primary bg-primary/[0.08]'
              : 'border-transparent text-muted-foreground hover:text-ink hover:bg-canvas/60'
          }`}
        >
          <ShieldCheck size={13} className={isAuto ? 'text-primary' : ''} />
          <span className={isAuto ? 'font-medium' : 'text-ink'}>{t(`approval.${value}` as const)}</span>
          <ChevronDown size={13} className="shrink-0 transition-transform group-data-[state=open]:rotate-180" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-64">
        <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground/70">
          {t('approval.label')}
        </DropdownMenuLabel>
        {MODES.map((m) => {
          const Icon = m.icon
          const on = value === m.id
          const auto = m.id === 'auto'
          return (
            <Fragment key={m.id}>
              {auto && <DropdownMenuSeparator />}
              <DropdownMenuItem onClick={() => onChange(m.id)}>
                {auto ? (
                  <span className={on ? 'lumi-orb shrink-0' : 'lumi-orb lumi-orb-idle shrink-0'} />
                ) : (
                  <Icon size={15} className="shrink-0 text-muted-foreground" />
                )}
                <span className="flex-1 min-w-0">
                  <span className={`block ${auto ? 'text-primary font-medium' : ''}`}>
                    {t(`approval.${m.id}` as const)}
                  </span>
                  <span className="block text-[11px] text-muted-foreground">
                    {auto && classifierLabel
                      ? `${t('approval.classifierPrefix')} ${classifierLabel}`
                      : t(`approval.${m.id}Desc` as const)}
                  </span>
                </span>
                <Check className={on ? 'text-primary' : 'opacity-0'} />
              </DropdownMenuItem>
            </Fragment>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

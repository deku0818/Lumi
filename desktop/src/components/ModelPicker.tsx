import { Check, ChevronDown } from 'lucide-react'
import type { ActiveModel, ProviderProfile } from '../types'
import { useI18n } from '../i18n'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'

// 输入框内的模型切换器（参考 Claude：发送按钮旁的模型 chip）。
// 向上弹出，按「供应商 → 模型」分组列出全部模型，点击即切换。仅切换，不配置供应商。
export function ModelPicker({
  model,
  providers,
  active,
  onSwitch,
}: {
  model: string
  providers: ProviderProfile[]
  active: ActiveModel
  onSwitch: (provider: string, model: string) => void
}) {
  const { t } = useI18n()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          title={t('model.switch')}
          className="group flex items-center gap-1 max-w-52 px-2 py-1 rounded-lg text-xs text-muted hover:text-ink hover:bg-canvas/60 transition outline-none"
        >
          <span className="truncate">{model || t('model.default')}</span>
          <ChevronDown size={13} className="shrink-0 transition-transform group-data-[state=open]:rotate-180" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-64 max-h-80 overflow-auto">
        {providers.length === 0 && (
          <div className="px-2 py-1.5 text-xs text-muted">{t('providers.none')}</div>
        )}
        {providers.map((p, i) => (
          <DropdownMenuGroup key={p.id}>
            {i > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted/70">
              {p.name}
            </DropdownMenuLabel>
            {p.models.length === 0 && (
              <div className="px-2 pb-1 text-xs text-muted/60">{t('model.noModels')}</div>
            )}
            {p.models.map((m) => {
              const on = active.provider === p.id && active.model === m
              return (
                <DropdownMenuItem key={m} onClick={() => onSwitch(p.id, m)}>
                  <Check className={on ? 'text-primary' : 'opacity-0'} />
                  <span className="truncate">{m}</span>
                </DropdownMenuItem>
              )
            })}
          </DropdownMenuGroup>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

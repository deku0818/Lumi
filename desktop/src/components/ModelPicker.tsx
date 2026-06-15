import { Fragment } from 'react'
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
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
} from '@/components/ui/dropdown-menu'

// 档位显示名：下发用原生值（小写），展示统一首字母大写（Low/High/Xhigh/On/Off…）
const levelLabel = (lv: string, t: (k: string) => string) =>
  lv === 'auto' ? t('effort.auto') : lv.charAt(0).toUpperCase() + lv.slice(1)

// 输入框内的模型切换器（参考 Claude Desktop，交互定稿见 .demos/lumi-effort-picker.html）：
// chip 显示「模型名 档位」；一级菜单仅三行——当前模型 ✓ / Effort|Thinking ›（该模型
// 无思考能力时不渲染）/ More models ›。档位选项完全由后端 thinking 数据驱动。
export function ModelPicker({
  model,
  providers,
  active,
  onSwitch,
  onSwitchEffort,
}: {
  model: string
  providers: ProviderProfile[]
  active: ActiveModel
  onSwitch: (provider: string, model: string) => void
  onSwitchEffort: (level: string) => void
}) {
  const { t } = useI18n()
  const activeProfile = providers.find((p) => p.id === active.provider)
  const thinking = activeProfile?.thinking?.[active.model]
  const control = thinking?.control ?? 'none'
  const levels = thinking?.levels ?? ['auto']
  const hasControl = control !== 'none'
  const isToggle = control === 'toggle'
  // toggle 型未设置（auto）时不下发参数 = 模型默认行为；这类模型默认开思考，
  // UI 按 On 展示（用户拨动后变为显式下发）
  const stored = thinking?.effort ?? 'auto'
  const effort = isToggle && stored === 'auto' ? 'on' : stored

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          title={t('model.switch')}
          className="group flex items-center gap-1.5 max-w-60 px-2 py-1 rounded-lg text-xs text-muted-foreground hover:text-ink hover:bg-canvas/60 transition outline-none"
        >
          <span className="truncate">{model || t('model.default')}</span>
          {stored !== 'auto' && (
            <span className={stored === 'ultra' ? 'text-primary font-medium' : 'opacity-70'}>
              {levelLabel(stored, t)}
            </span>
          )}
          <ChevronDown size={13} className="shrink-0 transition-transform group-data-[state=open]:rotate-180" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-60">
        {/* 当前模型 */}
        <DropdownMenuItem className="pointer-events-none">
          <div className="flex-1 min-w-0">
            <div className="truncate">{active.model || t('model.default')}</div>
            {activeProfile && (
              <div className="text-[11px] text-muted-foreground">{activeProfile.name}</div>
            )}
          </div>
          <Check className="text-primary" />
        </DropdownMenuItem>

        {/* Effort / Thinking 子菜单（无思考能力的模型不渲染） */}
        {hasControl && (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <span className="flex-1">{isToggle ? 'Thinking' : 'Effort'}</span>
              <span className="text-xs text-muted-foreground mr-1">
                {levelLabel(effort, t)}
              </span>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-56">
              <div className="px-2 py-1.5 text-[11px] text-muted-foreground border-b border-line/60 mb-1">
                {isToggle ? t('effort.toggleDesc') : t('effort.desc')}
              </div>
              {levels.map((lv) =>
                lv === 'ultra' ? (
                  // Ultra = Lumi 顶档（思考拉满 + 解锁 workflow 编排）：分隔线 + 呼吸金光点
                  // + 副标题，与原生档位区分（一静一动：光点动、文字静）。
                  <Fragment key="ultra">
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => onSwitchEffort('ultra')}>
                      <span className="lumi-orb shrink-0" />
                      <span className="flex-1">
                        <span className="text-primary font-medium">{levelLabel('ultra', t)}</span>
                        <span className="block text-[11px] text-muted-foreground">
                          {t('effort.ultraDesc')}
                        </span>
                      </span>
                      <Check className={effort === 'ultra' ? 'text-primary' : 'opacity-0'} />
                    </DropdownMenuItem>
                  </Fragment>
                ) : (
                  <DropdownMenuItem key={lv} onClick={() => onSwitchEffort(lv)}>
                    <span className="flex-1">{levelLabel(lv, t)}</span>
                    <Check className={effort === lv ? 'text-primary' : 'opacity-0'} />
                  </DropdownMenuItem>
                ),
              )}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        )}

        {/* More models 子菜单 */}
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>{t('model.more')}</DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="w-60 max-h-80 overflow-auto">
            {providers.length === 0 && (
              <div className="px-2 py-1.5 text-xs text-muted-foreground">
                {t('providers.none')}
              </div>
            )}
            {providers.map((p, i) => (
              <DropdownMenuGroup key={p.id}>
                {i > 0 && <DropdownMenuSeparator />}
                <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground/70">
                  {p.name}
                </DropdownMenuLabel>
                {p.models.length === 0 && (
                  <div className="px-2 pb-1 text-xs text-muted-foreground/60">
                    {t('model.noModels')}
                  </div>
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
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

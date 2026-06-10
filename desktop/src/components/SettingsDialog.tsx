import { SlidersHorizontal, Boxes, Monitor, Sun, Moon, type LucideIcon } from 'lucide-react'
import type { ActiveModel, ProviderProfile } from '../types'
import type { ThemePref } from '../theme'
import { useI18n } from '../i18n'
import { ProvidersPanel } from './ProvidersPanel'
import { Switch } from '@/components/ui/switch'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'

type TestResult = { ok: boolean; error?: string; latency_ms?: number }

// 设置弹窗：左侧导航 + 右侧面板（参考 Claude 桌面设置）。
// general：外观（主题）+ 语言；models：模型供应商管理（原输入框模型选择器迁移至此）。
export function SettingsDialog({
  themePref,
  setThemePref,
  notify,
  setNotify,
  profiles,
  active,
  onSwitch,
  onSave,
  onDelete,
  onTest,
  onClose,
}: {
  themePref: ThemePref
  setThemePref: (p: ThemePref) => void
  notify: boolean
  setNotify: (v: boolean) => void
  profiles: ProviderProfile[]
  active: ActiveModel
  onSwitch: (provider: string, model: string) => void
  onSave: (draft: { id?: string; name: string; base_url: string; api_key: string; models: string[] }) => void
  onDelete: (id: string) => void
  onTest: (baseUrl: string, apiKey: string, model: string) => Promise<TestResult>
  onClose: () => void
}) {
  const { t } = useI18n()
  const navClass =
    'justify-start gap-2.5 px-2.5 py-1.5 rounded-lg flex-none h-auto border-transparent text-muted hover:text-ink hover:bg-line/40 after:hidden focus-visible:ring-0 focus-visible:outline-none data-[state=active]:bg-line data-[state=active]:text-ink data-[state=active]:shadow-none'

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        showCloseButton
        className="sm:max-w-3xl w-full h-[34rem] p-0 gap-0 overflow-hidden flex"
      >
        <DialogTitle className="sr-only">{t('settings.title')}</DialogTitle>
        <Tabs defaultValue="general" orientation="vertical" className="flex h-full w-full gap-0">
          <TabsList
            variant="line"
            className="w-48 h-full group-data-vertical/tabs:h-full shrink-0 flex-col items-stretch justify-start gap-0.5 rounded-none bg-canvas border-r border-line/30 p-3"
          >
            <div className="px-2 pt-1 pb-2 text-xs font-medium text-muted uppercase tracking-wide">
              {t('settings.title')}
            </div>
            <TabsTrigger value="general" className={navClass}>
              <SlidersHorizontal />
              {t('settings.general')}
            </TabsTrigger>
            <TabsTrigger value="models" className={navClass}>
              <Boxes />
              {t('settings.models')}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="general" className="flex-1 min-w-0 overflow-auto px-6 pb-6 pt-12 mt-0">
            <GeneralPanel
              themePref={themePref}
              setThemePref={setThemePref}
              notify={notify}
              setNotify={setNotify}
            />
          </TabsContent>
          <TabsContent value="models" className="flex-1 min-w-0 overflow-auto px-6 pb-6 pt-12 mt-0">
            <ProvidersPanel
              profiles={profiles}
              active={active}
              onSwitch={onSwitch}
              onSave={onSave}
              onDelete={onDelete}
              onTest={onTest}
            />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

function GeneralPanel({
  themePref,
  setThemePref,
  notify,
  setNotify,
}: {
  themePref: ThemePref
  setThemePref: (p: ThemePref) => void
  notify: boolean
  setNotify: (v: boolean) => void
}) {
  const { t } = useI18n()
  return (
    <div>
      <h3 className="text-base font-medium mb-2">{t('settings.preferences')}</h3>
      <Row label={t('settings.appearance')}>
        <Segmented
          value={themePref}
          onChange={setThemePref}
          options={[
            { val: 'system', icon: Monitor, title: t('settings.theme.system') },
            { val: 'light', icon: Sun, title: t('settings.theme.light') },
            { val: 'dark', icon: Moon, title: t('settings.theme.dark') },
          ]}
        />
      </Row>

      <h3 className="text-base font-medium mt-7 mb-2">{t('settings.notifications')}</h3>
      <Row label={t('settings.respDone')} hint={t('settings.respDoneHint')}>
        <Switch checked={notify} onCheckedChange={setNotify} />
      </Row>
    </div>
  )
}

function Row({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-3 border-b border-line/20">
      <div className="min-w-0">
        <div className="text-sm text-ink/90">{label}</div>
        {hint && <div className="text-xs text-muted mt-0.5">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}


// 分段控件：图标或文字选项，选中态填充 surface。
function Segmented<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T
  onChange: (v: T) => void
  options: { val: T; icon?: LucideIcon; label?: string; title?: string }[]
}) {
  return (
    <div className="flex items-center gap-0.5 p-0.5 rounded-lg bg-canvas/60 border border-line/30">
      {options.map((o) => {
        const on = o.val === value
        return (
          <button
            key={o.val}
            onClick={() => onChange(o.val)}
            title={o.title}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-sm transition ${
              on ? 'bg-surface text-ink shadow-sm' : 'text-muted hover:text-ink'
            }`}
          >
            {o.icon && <o.icon size={15} className="shrink-0" />}
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

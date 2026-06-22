import { SlidersHorizontal, Boxes, Server, Monitor, Sun, Moon, Minus, Plus, type LucideIcon } from 'lucide-react'
import type { Gateway } from '../gateway'
import type { ThemePref } from '../theme'
import { type FontPref, DEFAULT_SIZE, MIN_SIZE, MAX_SIZE } from '../font'
import { useI18n } from '../i18n'
import { ProvidersPanel } from './ProvidersPanel'
import { BackendsPanel } from './BackendsPanel'
import { FontPicker } from './FontPicker'
import { Switch } from '@/components/ui/switch'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'

// 设置弹窗：左侧导航 + 右侧面板（参考 Claude 桌面设置）。
// general：外观（主题）+ 语言；models：模型供应商管理（原输入框模型选择器迁移至此）。
export function SettingsDialog({
  themePref,
  setThemePref,
  uiFont,
  setUiFont,
  notify,
  setNotify,
  recentLimit,
  setRecentLimit,
  machines,
  gwFor,
  onProvidersChanged,
  onClose,
}: {
  themePref: ThemePref
  setThemePref: (p: ThemePref) => void
  uiFont: FontPref
  setUiFont: (p: FontPref) => void
  notify: boolean
  setNotify: (v: boolean) => void
  recentLimit: number
  setRecentLimit: (n: number) => void
  machines: { id: string; name: string }[]
  gwFor: (id: string) => Gateway | undefined
  onProvidersChanged: (machine: string) => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const navClass =
    'justify-start gap-2.5 px-2.5 py-1.5 rounded-lg flex-none h-auto border-transparent text-muted-foreground hover:text-ink hover:bg-line/40 after:hidden focus-visible:ring-0 focus-visible:outline-none data-[state=active]:bg-line data-[state=active]:text-ink data-[state=active]:shadow-none'

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
            <div className="px-2 pt-1 pb-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
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
            <TabsTrigger value="connections" className={navClass}>
              <Server />
              {t('settings.connections')}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="general" className="flex-1 min-w-0 overflow-auto px-6 pb-6 pt-12 mt-0">
            <GeneralPanel
              themePref={themePref}
              setThemePref={setThemePref}
              uiFont={uiFont}
              setUiFont={setUiFont}
              notify={notify}
              setNotify={setNotify}
              recentLimit={recentLimit}
              setRecentLimit={setRecentLimit}
            />
          </TabsContent>
          <TabsContent value="models" className="flex-1 min-w-0 overflow-auto px-6 pb-6 pt-12 mt-0">
            <ProvidersPanel machines={machines} gwFor={gwFor} onChanged={onProvidersChanged} />
          </TabsContent>
          <TabsContent value="connections" className="flex-1 min-w-0 overflow-auto px-6 pb-6 pt-12 mt-0">
            <BackendsPanel />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

function GeneralPanel({
  themePref,
  setThemePref,
  uiFont,
  setUiFont,
  notify,
  setNotify,
  recentLimit,
  setRecentLimit,
}: {
  themePref: ThemePref
  setThemePref: (p: ThemePref) => void
  uiFont: FontPref
  setUiFont: (p: FontPref) => void
  notify: boolean
  setNotify: (v: boolean) => void
  recentLimit: number
  setRecentLimit: (n: number) => void
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
      <Row label={t('settings.uiFont')} hint={t('settings.uiFontHint')}>
        <FontPicker value={uiFont.family} onChange={(family) => setUiFont({ ...uiFont, family })} />
      </Row>
      <Row label={t('settings.fontSize')} hint={t('settings.fontSizeHint')}>
        <SizeStepper value={uiFont.size} onChange={(size) => setUiFont({ ...uiFont, size })} />
      </Row>

      <h3 className="text-base font-medium mt-7 mb-2">{t('settings.sessions')}</h3>
      <Row label={t('settings.recentLimit')} hint={t('settings.recentLimitHint')}>
        <RecentStepper value={recentLimit} onChange={setRecentLimit} />
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
        {hint && <div className="text-xs text-muted-foreground mt-0.5">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}


// 内联药丸控件容器（分段控件 / 字号步进器共用）
const PILL_WRAP = 'flex items-center gap-0.5 p-0.5 rounded-lg bg-canvas/60 border border-line/30'
const STEP_BTN =
  'flex items-center justify-center w-7 h-7 rounded-md text-muted-foreground hover:text-ink hover:bg-line/30 transition disabled:opacity-30 disabled:hover:bg-transparent'

const clampSize = (n: number) => Math.min(MAX_SIZE, Math.max(MIN_SIZE, n))

// 字号步进器：− [n] + ，限定 MIN_SIZE..MAX_SIZE；点击数字重置为默认。
function SizeStepper({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  return (
    <div className={PILL_WRAP}>
      <button className={STEP_BTN} onClick={() => onChange(clampSize(value - 1))} disabled={value <= MIN_SIZE}>
        <Minus size={14} />
      </button>
      <button
        onClick={() => onChange(DEFAULT_SIZE)}
        title={`${DEFAULT_SIZE}px`}
        className="min-w-12 text-center text-sm tabular-nums text-ink hover:text-primary transition"
      >
        {value}px
      </button>
      <button className={STEP_BTN} onClick={() => onChange(clampSize(value + 1))} disabled={value >= MAX_SIZE}>
        <Plus size={14} />
      </button>
    </div>
  )
}

// 「最近」显示条数步进器：− [n 条] + ，范围 5–100、步进 5；点数字回默认 20。
const RECENT_MIN = 5
const RECENT_MAX = 100
const RECENT_STEP = 5
const RECENT_DEFAULT = 20
const clampRecent = (n: number) => Math.min(RECENT_MAX, Math.max(RECENT_MIN, n))
function RecentStepper({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  const { t } = useI18n()
  return (
    <div className={PILL_WRAP}>
      <button
        className={STEP_BTN}
        onClick={() => onChange(clampRecent(value - RECENT_STEP))}
        disabled={value <= RECENT_MIN}
      >
        <Minus size={14} />
      </button>
      <button
        onClick={() => onChange(RECENT_DEFAULT)}
        title={String(RECENT_DEFAULT)}
        className="min-w-12 text-center text-sm tabular-nums text-ink hover:text-primary transition"
      >
        {t('settings.recentN', { n: value })}
      </button>
      <button
        className={STEP_BTN}
        onClick={() => onChange(clampRecent(value + RECENT_STEP))}
        disabled={value >= RECENT_MAX}
      >
        <Plus size={14} />
      </button>
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
    <div className={PILL_WRAP}>
      {options.map((o) => {
        const on = o.val === value
        return (
          <button
            key={o.val}
            onClick={() => onChange(o.val)}
            title={o.title}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-sm transition ${
              on ? 'bg-surface text-ink shadow-sm' : 'text-muted-foreground hover:text-ink'
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

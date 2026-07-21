import { SlidersHorizontal, Boxes, Server, Send, Plug, Info, Monitor, Sun, Moon, Minus, Plus } from 'lucide-react'
import type { Gateway } from '../gateway'
import type { ThemePref } from '../theme'
import { type FontPref, DEFAULT_SIZE, MIN_SIZE, MAX_SIZE } from '../font'
import { useI18n } from '../i18n'
import { ProvidersPanel } from './ProvidersPanel'
import { ChannelsPanel } from './ChannelsPanel'
import { McpPanel } from './McpPanel'
import { BackendsPanel } from './BackendsPanel'
import { AboutPanel } from './AboutPanel'
import { FontPicker } from './FontPicker'
import { Section, SectionGroup, Row, SegmentedControl, segmentShell } from './SettingsKit'
import { Switch } from '@/components/ui/switch'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'

// 设置弹窗：左侧导航 + 右侧面板（参考 Claude 桌面设置）。
// general：外观（主题）+ 语言；models：模型供应商管理（原输入框模型选择器迁移至此）。
export function SettingsDialog({
  initialTab,
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
  initialTab?: 'general' | 'models' | 'channels' | 'connections' | 'mcp' | 'about' // 打开时定位的 tab
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
        <Tabs defaultValue={initialTab ?? 'general'} orientation="vertical" className="flex h-full w-full gap-0">
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
            <TabsTrigger value="channels" className={navClass}>
              <Send />
              {t('settings.channels')}
            </TabsTrigger>
            <TabsTrigger value="mcp" className={navClass}>
              <Plug />
              {t('settings.mcp')}
            </TabsTrigger>
            <TabsTrigger value="connections" className={navClass}>
              <Server />
              {t('settings.connections')}
            </TabsTrigger>
            <TabsTrigger value="about" className={navClass}>
              <Info />
              {t('settings.about')}
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
          <TabsContent value="channels" className="flex-1 min-w-0 overflow-auto px-6 pb-6 pt-12 mt-0">
            <ChannelsPanel machines={machines} gwFor={gwFor} />
          </TabsContent>
          <TabsContent value="mcp" className="flex-1 min-w-0 overflow-auto px-6 pb-6 pt-12 mt-0">
            <McpPanel machines={machines} gwFor={gwFor} />
          </TabsContent>
          <TabsContent value="connections" className="flex-1 min-w-0 overflow-auto px-6 pb-6 pt-12 mt-0">
            <BackendsPanel />
          </TabsContent>
          <TabsContent value="about" className="flex-1 min-w-0 overflow-auto px-6 pb-6 pt-12 mt-0">
            <AboutPanel />
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
    <SectionGroup>
      <Section title={t('settings.preferences')}>
        <Row label={t('settings.appearance')}>
          <SegmentedControl
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
      </Section>

      <Section title={t('settings.sessions')}>
        <Row label={t('settings.recentLimit')} hint={t('settings.recentLimitHint')}>
          <RecentStepper value={recentLimit} onChange={setRecentLimit} />
        </Row>
      </Section>

      <Section title={t('settings.notifications')}>
        <Row label={t('settings.respDone')} hint={t('settings.respDoneHint')}>
          <Switch checked={notify} onCheckedChange={setNotify} />
        </Row>
      </Section>
    </SectionGroup>
  )
}

// 字号 / 条数步进器容器：复用 SegmentedControl 的药丸外壳（segmentShell），只加垂直居中，避免边框漂移
const PILL_WRAP = `${segmentShell} items-center`
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

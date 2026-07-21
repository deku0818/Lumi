import { Loader2 } from 'lucide-react'
import { useI18n } from '../i18n'
import { useUpdateState, RELEASES_URL } from '../update'
import type { UpdateState } from '../types'
import { Section, SectionGroup, Row } from './SettingsKit'
import { Button } from '@/components/ui/button'

// 关于 + 软件更新。状态机在主进程，这里只把 UpdateState 映射成一行「状态 + 动作」。
export function AboutPanel() {
  const { t } = useI18n()
  const update = useUpdateState()

  return (
    <SectionGroup>
      <div className="flex items-center gap-3.5">
        <div className="w-9 h-9 grid place-items-center shrink-0">
          <div className="lumi-orb lumi-orb-idle scale-[2.4]" />
        </div>
        <div>
          <div className="text-[17px] font-semibold tracking-tight text-ink">Lumi</div>
          <div className="text-xs text-muted-foreground mt-0.5 tabular-nums">
            {t('about.version', { v: update?.current ?? '—' })}
          </div>
        </div>
      </div>

      <Section title={t('about.update')}>
        {update && (
          <Row label={<UpdateLabel s={update} />} hint={<UpdateHint s={update} />}>
            <UpdateAction s={update} />
          </Row>
        )}
        <Row label={t('about.releaseNotes')}>
          <a href={RELEASES_URL} target="_blank" rel="noreferrer">
            <Button variant="outline" size="sm">
              {t('about.viewOnGitHub')}
            </Button>
          </a>
        </Row>
      </Section>
    </SectionGroup>
  )
}

// 以下三个组件必须留在模块级：定义在 AboutPanel 函数体内的话，每次渲染都是新的组件
// 类型，React 会卸载重建整棵子树 —— 下载进度条的宽度过渡会因此永远不生效。
const dotClass = 'w-[7px] h-[7px] rounded-full shrink-0'

function UpdateLabel({ s }: { s: UpdateState }) {
  const { t } = useI18n()
  return (
    <span className="flex items-center gap-2">
      {s.status === 'checking' || s.status === 'downloading' ? (
        <Loader2 size={13} className="animate-spin text-primary" />
      ) : s.status === 'error' ? (
        <span className={`${dotClass} bg-[var(--color-error)]`} />
      ) : s.status === 'available' || s.status === 'ready' ? (
        <span className={`${dotClass} bg-primary animate-pulse`} />
      ) : (
        <span className={`${dotClass} bg-[var(--color-success)]`} />
      )}
      {t(`about.status.${s.status}`, { v: s.version ?? '' })}
    </span>
  )
}

function UpdateHint({ s }: { s: UpdateState }) {
  const { t } = useI18n()
  if (s.status === 'downloading') return `${s.percent ?? 0}%`
  if (s.status === 'available' && s.manual) return t('about.manualHint')
  // ready 也可能带 error：安装失败回滚后包仍在本地，此处呈现失败原因
  if (s.error) return s.error
  return null
}

function UpdateAction({ s }: { s: UpdateState }) {
  const { t } = useI18n()
  const api = window.lumi?.update
  if (s.status === 'downloading') {
    return (
      <div className="w-[150px] h-[5px] rounded-full bg-line overflow-hidden">
        <div
          className="h-full bg-primary rounded-full transition-[width] duration-300"
          style={{ width: `${s.percent ?? 0}%` }}
        />
      </div>
    )
  }
  // 文案跟着状态走而非 manual：ready 意味着包已在本地，动作就是重启装上；
  // available + manual 才是「本机装不了，去浏览器下」。
  if (s.status === 'ready' || (s.status === 'available' && s.manual)) {
    return (
      <Button size="sm" onClick={() => api?.install()}>
        {t(s.status === 'ready' ? 'about.restart' : 'about.download')}
      </Button>
    )
  }
  return (
    <Button variant="outline" size="sm" disabled={s.status === 'checking'} onClick={() => api?.check()}>
      {t(s.status === 'error' ? 'about.retry' : 'about.check')}
    </Button>
  )
}

import { useEffect, useState } from 'react'
import { Plus, Pencil, Trash2, Server } from 'lucide-react'
import type { BackendRemote, BackendsState } from '../types'
import { useI18n } from '../i18n'
import { machineColor } from '@/lib/utils'
import { useMachine } from './MachineTabs'
import { Empty, EntityCard, Field, FormModal, Section, SectionGroup, StatusDot, TextInput } from './SettingsKit'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'

// 一次性测试连接：开一条裸 WS（带 ?token=），收到首帧=通、1008=鉴权失败、其余=不可达。
type TestState = { status: 'idle' | 'testing' | 'ok' | 'fail'; msgKey?: string }

function testConnection(url: string, token: string): Promise<TestState> {
  return new Promise((resolve) => {
    let ws: WebSocket
    let timer: ReturnType<typeof setTimeout>
    let grace: ReturnType<typeof setTimeout>
    let done = false
    // 唯一出口：定时器与 socket 都在这里收尾，各分支只管给结论
    const finish = (msgKey: string, ok = false) => {
      if (done) return
      done = true
      clearTimeout(timer)
      clearTimeout(grace)
      try {
        ws.close()
      } catch {
        /* 已关闭/未建立：忽略 */
      }
      resolve({ status: ok ? 'ok' : 'fail', msgKey })
    }
    const sep = url.includes('?') ? '&' : '?'
    try {
      ws = new WebSocket(`${url}${sep}token=${encodeURIComponent(token)}`)
    } catch {
      resolve({ status: 'fail', msgKey: 'backends.unreachable' })
      return
    }
    timer = setTimeout(() => finish('backends.timeout'), 6000)
    // onopen 不能直接判成功：服务端「先 accept 再校验 token」（accept 前 close 客户端只见
    // 1006，分不清鉴权失败与不可达），open 必然先于鉴权结论到达——拿它判成功的话 token
    // 填错也报「连接成功」。但也不能只等首帧：首帧 gateway.ready 要等整个 bridge 初始化
    // （工具装配 / MCP / checkpointer），冷启动的机器可能十几秒，会被误报成超时。
    // 故取两者之先：收到首帧，或 open 后 1.5 秒没被 close（1008 是紧随 accept 的毫秒级事件）。
    ws.onopen = () => {
      grace = setTimeout(() => finish('backends.ok', true), 1500)
    }
    ws.onmessage = () => finish('backends.ok', true)
    ws.onclose = (ev) => finish(ev.code === 1008 ? 'backends.authFail' : 'backends.unreachable')
    ws.onerror = () => finish('backends.unreachable')
  })
}

// 设置 → 连接：管理本地 + 远程机器，选活动后端（切换后整页重连）。
// 自包含：直接走 window.lumi.backends，无需 App 透传 props。
export function BackendsPanel() {
  const { t } = useI18n()
  const [state, setState] = useState<BackendsState | null>(null)
  const [editing, setEditing] = useState<Partial<BackendRemote> | null>(null)

  const api = window.lumi.backends
  const reload = () => api?.list().then(setState)
  useEffect(() => {
    void reload()
  }, [])

  // 方案甲：所有机器同时连接，无"活动/切换"。增删后广播事件，App 据此开/关控制连接并刷新。
  // reconnectId：编辑了某机器地址/token 时带上，App 据此换址重连（仅 syncBackends 不会重建已有连接）。
  const notifyChanged = (reconnectId?: string) =>
    window.dispatchEvent(new CustomEvent('lumi:backends-changed', { detail: { reconnectId } }))
  const remove = async (id: string) => {
    if (!api) return
    setState(await api.remove(id))
    notifyChanged()
  }
  // 开关连接：enabled=false 表示已配置但不连接（持久化进 backends.json）
  const toggle = async (id: string, enabled: boolean) => {
    if (!api) return
    setState(await api.save({ id, enabled }))
    notifyChanged()
  }

  if (!state) return null

  return (
    <div>
      <SectionGroup>
        <Section title={t('settings.connections')} desc={t('backends.desc')}>
          {/* 本地 sidecar：恒在、不可删 */}
          <MachineRow id="local" name={t('backends.local')} sub={t('backends.localHint')} color="var(--color-accent)" />
        </Section>

        <Section
          title={t('backends.remotes')}
          action={
            <Button variant="outline" size="sm" onClick={() => setEditing({})}>
              <Plus />
              {t('backends.add')}
            </Button>
          }
        >
          {state.remotes.length === 0 ? (
            <Empty>{t('backends.empty')}</Empty>
          ) : (
            <div className="space-y-2">
              {state.remotes.map((r) => (
                <MachineRow
                  key={r.id}
                  id={r.id}
                  name={r.name || r.url}
                  sub={r.url}
                  mono
                  color={machineColor(r.id, [{ id: 'local' }, ...state.remotes])}
                  enabled={r.enabled !== false}
                  onEdit={() => setEditing(r)}
                  onDelete={() => remove(r.id)}
                  onToggle={(v) => toggle(r.id, v)}
                />
              ))}
            </div>
          )}
        </Section>
      </SectionGroup>

      {editing !== null && (
        <RemoteForm
          initial={editing}
          onCancel={() => setEditing(null)}
          onSaved={async (draft) => {
            const next = await api?.save(draft)
            if (next) setState(next)
            setEditing(null)
            // 编辑现有机器（draft.id 存在）→ 换址重连；新增则交给 syncBackends 建连
            notifyChanged(draft.id)
          }}
        />
      )}
    </div>
  )
}

// 一张卡 = 一台机器（统一实体卡语法）。副标题平时是地址，连不上时**就地换成失败原因**
// （地址退到 title 悬停）：出错时最该看的是原因，地址点「编辑」随时能看。显示的是实时
// 连接态而非保存那一刻的快照，机器自行恢复后红字自己消失，故不需要「重试」按钮。
function MachineRow({
  id,
  name,
  sub,
  mono,
  color,
  enabled = true,
  onEdit,
  onDelete,
  onToggle,
}: {
  id: string
  name: string
  sub: string
  mono?: boolean // 副标题是地址（mono 排版）；本机的说明文案则否
  color: string
  enabled?: boolean
  onEdit?: () => void
  onDelete?: () => void
  onToggle?: (enabled: boolean) => void
}) {
  const { t } = useI18n()
  const { scope, error } = useMachine(id)
  // 已关掉连接的机器不谈连接态（它本来就没连），只按「停用」淡化
  const live = enabled ? scope : undefined
  const failed = live === 'stopped'
  const reason = failed ? t(error === 'auth' ? 'backends.authFail' : 'backends.unreachable') : ''
  // 状态点：机器色实心=已连接（各机器一色，与侧栏一致）、红=失败、空心=停用
  const dot = !enabled ? (
    <StatusDot tone="hollow" />
  ) : failed ? (
    <StatusDot tone="error" />
  ) : (
    <StatusDot color={color} pulse={live === 'retrying'} />
  )
  // 地址被截断/被失败原因或「连接中」顶掉时，悬停恒能看到全文（subtitleTitle）
  return (
    <EntityCard
      dim={!enabled}
      icon={<Server size={16} />}
      title={name}
      meta={dot}
      subtitle={
        // mono 只作用于真在展示地址的分支；失败原因/「连接中」是普通文案
        <span className={reason ? 'text-error' : mono && live !== 'retrying' ? 'font-mono' : undefined}>
          {reason || (live === 'retrying' ? t('common.connecting') : sub)}
        </span>
      }
      subtitleTitle={sub}
      actions={
        (onEdit || onDelete) && (
          <>
            {onEdit && (
              <Button variant="ghost" size="icon-sm" onClick={onEdit} className="text-muted-foreground">
                <Pencil />
              </Button>
            )}
            {onDelete && (
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={onDelete}
                className="text-muted-foreground hover:text-error"
              >
                <Trash2 />
              </Button>
            )}
          </>
        )
      }
      trailing={onToggle && <Switch checked={enabled} onCheckedChange={onToggle} />}
    />
  )
}

function RemoteForm({
  initial,
  onCancel,
  onSaved,
}: {
  initial: Partial<BackendRemote>
  onCancel: () => void
  onSaved: (draft: Partial<BackendRemote>) => void
}) {
  const { t } = useI18n()
  const [name, setName] = useState(initial.name ?? '')
  const [url, setUrl] = useState(initial.url ?? '')
  const [token, setToken] = useState(initial.token ?? '')
  const [test, setTest] = useState<TestState>({ status: 'idle' })

  const valid = url.trim().startsWith('ws')
  const runTest = async () => {
    setTest({ status: 'testing' })
    setTest(await testConnection(url.trim(), token))
  }


  const footer = (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={runTest}
        disabled={!valid || test.status === 'testing'}
      >
        <span className={`lumi-orb ${test.status === 'testing' ? '' : 'lumi-orb-idle'}`} style={{ width: 11, height: 11 }} />
        {test.status === 'testing' ? t('backends.testing') : t('backends.test')}
      </Button>
      {test.status === 'ok' && <span className="text-xs text-success">{t(test.msgKey!)}</span>}
      {test.status === 'fail' && <span className="text-xs text-error">{t(test.msgKey!)}</span>}
      <div className="flex-1" />
      <Button variant="ghost" onClick={onCancel}>
        {t('backends.cancel')}
      </Button>
      {/* 保存不额外跑一次检测：保存后立刻会为这台机器建控制连接，那本身就是一次真检测，
          结论（连接中 / 已连接 / 失败原因）长期显示在列表行上。在这里再连一次只会让按钮
          空转最多 6 秒，且结果随弹窗关闭一起丢掉——没人看得到 */}
      <Button
        onClick={() => onSaved({ id: initial.id, name: name.trim() || url.trim(), url: url.trim(), token })}
        disabled={!valid}
      >
        {t('backends.save')}
      </Button>
    </>
  )

  return (
    <FormModal
      onClose={onCancel}
      title={initial.id ? t('backends.edit') : t('backends.add')}
      footer={footer}
    >
      <div className="space-y-3.5">
        <Field label={t('backends.name')}>
          <TextInput value={name} placeholder={t('backends.namePh')} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t('backends.url')}>
          <TextInput value={url} placeholder="wss://dev.example.com/ws" onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <Field label={t('backends.token')}>
          <TextInput password value={token} placeholder={t('backends.tokenPh')} onChange={(e) => setToken(e.target.value)} />
        </Field>
      </div>
    </FormModal>
  )
}

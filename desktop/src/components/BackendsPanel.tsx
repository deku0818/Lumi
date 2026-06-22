import { useEffect, useState } from 'react'
import { Plus, Pencil, Trash2, Server } from 'lucide-react'
import type { BackendRemote, BackendsState } from '../types'
import { useI18n } from '../i18n'
import { machineColor } from '@/lib/utils'
import { Switch } from '@/components/ui/switch'

// 一次性测试连接：开一条裸 WS（带 ?token=），open=通、1008=鉴权失败、其余=不可达。
type TestState = { status: 'idle' | 'testing' | 'ok' | 'fail'; msgKey?: string }

function testConnection(url: string, token: string): Promise<TestState> {
  return new Promise((resolve) => {
    let ws: WebSocket
    let done = false
    const finish = (s: TestState) => {
      if (done) return
      done = true
      try {
        ws.close()
      } catch {
        /* noop */
      }
      resolve(s)
    }
    const sep = url.includes('?') ? '&' : '?'
    try {
      ws = new WebSocket(`${url}${sep}token=${encodeURIComponent(token)}`)
    } catch {
      resolve({ status: 'fail', msgKey: 'backends.unreachable' })
      return
    }
    const timer = setTimeout(() => finish({ status: 'fail', msgKey: 'backends.timeout' }), 6000)
    ws.onopen = () => {
      clearTimeout(timer)
      finish({ status: 'ok', msgKey: 'backends.ok' })
    }
    ws.onclose = (ev) => {
      clearTimeout(timer)
      finish({ status: 'fail', msgKey: ev.code === 1008 ? 'backends.authFail' : 'backends.unreachable' })
    }
    ws.onerror = () => {
      clearTimeout(timer)
      finish({ status: 'fail', msgKey: 'backends.unreachable' })
    }
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
      <h3 className="text-base font-medium mb-1">{t('settings.connections')}</h3>
      <p className="text-xs text-muted-foreground mb-3">{t('backends.desc')}</p>

      {/* 本地 sidecar：恒在、不可删 */}
      <MachineRow name={t('backends.local')} sub={t('backends.localHint')} color="var(--color-accent)" />

      <div className="mt-5 mb-1.5 text-xs text-muted-foreground">{t('backends.remotes')}</div>
      {state.remotes.length === 0 && editing === null && (
        <div className="text-sm text-muted-foreground/70 py-3">{t('backends.empty')}</div>
      )}
      {state.remotes.map((r) => (
        <MachineRow
          key={r.id}
          name={r.name || r.url}
          sub={r.url}
          color={machineColor(r.id, [{ id: 'local' }, ...state.remotes])}
          enabled={r.enabled !== false}
          onEdit={() => setEditing(r)}
          onDelete={() => remove(r.id)}
          onToggle={(v) => toggle(r.id, v)}
        />
      ))}

      {editing === null ? (
        <button
          onClick={() => setEditing({})}
          className="mt-3 flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-ink hover:bg-line/30 transition"
        >
          <Plus size={15} />
          {t('backends.add')}
        </button>
      ) : (
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

function MachineRow({
  name,
  sub,
  color,
  enabled = true,
  onEdit,
  onDelete,
  onToggle,
}: {
  name: string
  sub: string
  color: string
  enabled?: boolean
  onEdit?: () => void
  onDelete?: () => void
  onToggle?: (enabled: boolean) => void
}) {
  return (
    <div className={`group flex items-center gap-3 py-2.5 border-b border-line/20 ${enabled ? '' : 'opacity-50'}`}>
      <span
        className="shrink-0 size-2.5 rounded-full"
        style={
          enabled
            ? { background: color, boxShadow: `0 0 6px ${color}` }
            : { border: '1.5px solid var(--color-separator)' }
        }
      />
      <Server size={15} className="shrink-0 text-muted-foreground" />
      <div className="flex-1 min-w-0">
        <div className="text-sm text-ink/90 truncate">{name}</div>
        <div className="text-xs text-muted-foreground truncate">{sub}</div>
      </div>
      {onEdit && (
        <button
          onClick={onEdit}
          className="shrink-0 size-7 grid place-items-center rounded-md text-muted-foreground hover:text-ink hover:bg-line/30 transition opacity-0 group-hover:opacity-100"
        >
          <Pencil size={14} />
        </button>
      )}
      {onDelete && (
        <button
          onClick={onDelete}
          className="shrink-0 size-7 grid place-items-center rounded-md text-muted-foreground hover:text-error hover:bg-line/30 transition opacity-0 group-hover:opacity-100"
        >
          <Trash2 size={14} />
        </button>
      )}
      {onToggle && (
        <Switch checked={enabled} onCheckedChange={onToggle} className="shrink-0 ml-1" />
      )}
    </div>
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

  const inputCls =
    'w-full px-3 py-2 rounded-lg text-sm bg-canvas border border-line focus:border-primary/50 outline-none'

  return (
    <div className="mt-3 p-3.5 rounded-xl bg-surface/50 border border-line/40 flex flex-col gap-2.5">
      <Field label={t('backends.name')}>
        <input className={inputCls} value={name} placeholder={t('backends.namePh')} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label={t('backends.url')}>
        <input className={inputCls} value={url} placeholder="wss://dev.example.com/ws" onChange={(e) => setUrl(e.target.value)} />
      </Field>
      <Field label={t('backends.token')}>
        <input className={inputCls} type="password" value={token} placeholder={t('backends.tokenPh')} onChange={(e) => setToken(e.target.value)} />
      </Field>

      <div className="flex items-center gap-3 pt-1">
        <button
          onClick={runTest}
          disabled={!valid || test.status === 'testing'}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm bg-canvas border border-line hover:border-primary disabled:opacity-40 transition"
        >
          <span className={`lumi-orb ${test.status === 'testing' ? '' : 'lumi-orb-idle'}`} style={{ width: 11, height: 11 }} />
          {test.status === 'testing' ? t('backends.testing') : t('backends.test')}
        </button>
        {test.status === 'ok' && <span className="text-xs text-success">{t(test.msgKey!)}</span>}
        {test.status === 'fail' && <span className="text-xs text-error">{t(test.msgKey!)}</span>}

        <div className="flex-1" />
        <button onClick={onCancel} className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-ink transition">
          {t('backends.cancel')}
        </button>
        <button
          onClick={() => onSaved({ id: initial.id, name: name.trim() || url.trim(), url: url.trim(), token })}
          disabled={!valid}
          className="px-3.5 py-1.5 rounded-lg text-sm font-medium bg-primary text-canvas disabled:opacity-40 transition"
        >
          {t('backends.save')}
        </button>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      {children}
    </label>
  )
}

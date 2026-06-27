import { useCallback, useEffect, useState } from 'react'
import {
  Check,
  Pencil,
  Trash2,
  Plus,
  Loader2,
  ArrowLeft,
  HelpCircle,
  Shield,
  X,
} from 'lucide-react'
import type { ActiveModel, Classifier, ProviderProfile } from '../types'
import type { Gateway } from '../gateway'
import { useI18n } from '../i18n'
import { MachineTabs } from './MachineTabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'

type TestResult = { ok: boolean; error?: string; latency_ms?: number }
type RowTest = 'testing' | TestResult | undefined
type ModelRow = { id: number; name: string; test: RowTest }
type Form = { id?: string; name: string; base_url: string; api_key: string; models: ModelRow[] }

let _rid = 0
const newId = () => ++_rid

const emptyForm = (): Form => ({
  name: '',
  base_url: '',
  api_key: '',
  models: [{ id: newId(), name: '', test: undefined }],
})

const formFrom = (p: ProviderProfile): Form => ({
  id: p.id,
  name: p.name,
  base_url: p.base_url,
  api_key: p.api_key,
  models: (p.models.length ? p.models : ['']).map((m) => ({ id: newId(), name: m, test: undefined })),
})

// 模型提供商面板（设置 → 模型）。两个视图：
//   列表视图：右上角「添加提供商」，下方提供商卡片（模型 chip 可点切换、编辑、删除）。
//   表单视图：添加/编辑——一套连接 + 逐行模型（每行带「测试」与费用提示）。
export function ProvidersPanel({
  machines,
  gwFor,
  onChanged,
}: {
  machines: { id: string; name: string }[]
  gwFor: (id: string) => Gateway | undefined
  onChanged: (machine: string) => void
}) {
  const { t } = useI18n()
  // 方案甲「先选机器」：每台机器各自持有 providers（后端 providers.json）；按机器读写。
  const [machine, setMachine] = useState('local')
  const [profiles, setProfiles] = useState<ProviderProfile[]>([])
  const [active, setActive] = useState<ActiveModel>({ provider: '', model: '' })
  const [classifier, setClassifier] = useState<Classifier>({})
  const [form, setForm] = useState<Form | null>(null) // null = 列表视图

  const reload = useCallback(() => {
    gwFor(machine)
      ?.listProviders()
      .then((r) => {
        setProfiles(r.profiles ?? [])
        setActive(r.active ?? { provider: '', model: '' })
        setClassifier(r.classifier ?? {})
      })
      .catch(() => {})
  }, [gwFor, machine])

  useEffect(() => {
    reload()
  }, [reload])

  const gw = gwFor(machine)
  const apply = (r: {
    profiles?: ProviderProfile[]
    active?: ActiveModel
    classifier?: Classifier
  }) => {
    setProfiles(r.profiles ?? [])
    setActive(r.active ?? { provider: '', model: '' })
    // 删/改 provider 后端会规范化清掉失效的分类器指针，须同步回写避免 UI 陈旧
    setClassifier(r.classifier ?? {})
    onChanged(machine)
  }
  const onSwitch = (provider: string, model: string) =>
    gw?.setProvider(provider, model)
      .then((r) => {
        setActive(r.active)
        onChanged(machine)
      })
      .catch(() => {})
  // 分类器指针：传相同 (provider, model) 视为取消 → 回退「跟随会话模型」（空）
  const pickClassifier = (provider: string, model: string) => {
    const same = classifier.provider === provider && classifier.model === model
    gw?.setClassifier(same ? '' : provider, same ? '' : model)
      .then((r) => {
        setClassifier(r.classifier ?? {})
        onChanged(machine)
      })
      .catch(() => {})
  }
  const onSave = (draft: Partial<ProviderProfile>) => gw?.saveProvider(draft).then(apply).catch(() => {})
  const onDelete = (id: string) => gw?.deleteProvider(id).then(apply).catch(() => {})
  const onTest = (baseUrl: string, apiKey: string, model: string): Promise<TestResult> =>
    gw?.testProvider(baseUrl, apiKey, model) ??
    Promise.resolve({ ok: false, error: t('sidebar.disconnected') })

  if (form) {
    return (
      <ProviderForm
        form={form}
        setForm={setForm}
        onTest={onTest}
        onSubmit={(draft) => {
          onSave(draft)
          setForm(null)
        }}
        onCancel={() => setForm(null)}
      />
    )
  }

  return (
    <div>
      <MachineTabs machines={machines} value={machine} onChange={setMachine} />
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-medium">{t('providers.title')}</h3>
        <Button variant="outline" size="sm" onClick={() => setForm(emptyForm())}>
          <Plus />
          {t('common.add')}
        </Button>
      </div>

      {profiles.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground/80">{t('providers.none')}</div>
      ) : (
        <div className="space-y-2">
          {profiles.map((p) => (
            <div key={p.id} className="px-3 py-2 rounded-xl border border-line/40">
              <div className="flex items-center gap-2">
                <span className="flex-1 min-w-0 truncate text-sm font-medium">{p.name}</span>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setForm(formFrom(p))}
                  aria-label={t('providers.edit')}
                  className="shrink-0 text-muted-foreground"
                >
                  <Pencil />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => onDelete(p.id)}
                  aria-label={t('common.delete')}
                  className="shrink-0 text-muted-foreground hover:text-error"
                >
                  <Trash2 />
                </Button>
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {p.models.map((m) => {
                  const on = active.provider === p.id && active.model === m
                  return (
                    <ModelChip
                      key={m}
                      label={m}
                      selected={on}
                      title={on ? t('providers.inUse') : t('providers.switchHint')}
                      onClick={() => onSwitch(p.id, m)}
                    />
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 审批分类器模型（auto 模式专用，独立于会话模型）。无 provider 时不渲染 */}
      {profiles.length > 0 && (
        <div className="mt-7">
          <h3 className="flex items-center gap-2 text-sm font-medium">
            <Shield size={15} className="text-primary shrink-0" />
            {t('classifier.title')}
          </h3>
          <p className="mt-0.5 mb-3 text-[11.5px] text-muted-foreground">{t('classifier.desc')}</p>

          {/* 跟随会话模型（默认 = classifier 为空） */}
          <button
            onClick={() => pickClassifier('', '')}
            className={`w-full flex items-center gap-2 px-3 py-2 mb-2 rounded-xl border border-dashed transition ${
              !classifier.provider
                ? 'border-primary/40 bg-primary/5'
                : 'border-line/40 hover:bg-canvas/60'
            }`}
          >
            <span className={!classifier.provider ? 'lumi-orb-idle lumi-orb shrink-0' : 'size-2 rounded-full bg-muted-foreground/40 shrink-0'} />
            <span className="flex-1 min-w-0 text-left">
              <span className="block text-[13px]">{t('classifier.follow')}</span>
              <span className="block text-[11px] text-muted-foreground">{t('classifier.followHint')}</span>
            </span>
            {!classifier.provider && <Check size={14} className="text-primary shrink-0" />}
          </button>

          <div className="space-y-2">
            {profiles.map((p) => (
              <div key={p.id} className="px-3 py-2 rounded-xl border border-line/40">
                <div className="text-[13px] font-medium mb-1.5">{p.name}</div>
                <div className="flex flex-wrap gap-1.5">
                  {p.models.map((m) => (
                    <ModelChip
                      key={m}
                      label={m}
                      selected={classifier.provider === p.id && classifier.model === m}
                      title={t('classifier.use')}
                      onClick={() => pickClassifier(p.id, m)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// 可点的模型 chip（选中=品牌金描边+Check）。当前模型与分类器两处列表共用。
function ModelChip({
  label,
  selected,
  title,
  onClick,
}: {
  label: string
  selected: boolean
  title: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-xs border transition ${
        selected
          ? 'border-primary/50 bg-primary/10 text-primary'
          : 'border-line/40 text-muted-foreground hover:text-ink hover:bg-canvas/60'
      }`}
    >
      {selected && <Check size={12} />}
      {label}
    </button>
  )
}

function ProviderForm({
  form,
  setForm,
  onTest,
  onSubmit,
  onCancel,
}: {
  form: Form
  setForm: (f: Form) => void
  onTest: (baseUrl: string, apiKey: string, model: string) => Promise<TestResult>
  onSubmit: (draft: { id?: string; name: string; base_url: string; api_key: string; models: string[] }) => void
  onCancel: () => void
}) {
  const { t } = useI18n()
  const editing = form.id != null
  const validModels = form.models.map((m) => m.name.trim()).filter(Boolean)
  // 提供商名称、Base URL（必填）、至少一个模型
  const canSave = !!form.name.trim() && !!form.base_url.trim() && validModels.length > 0

  const patchModel = (id: number, patch: Partial<ModelRow>) =>
    setForm({ ...form, models: form.models.map((m) => (m.id === id ? { ...m, ...patch } : m)) })
  const addModel = () =>
    setForm({ ...form, models: [...form.models, { id: newId(), name: '', test: undefined }] })
  const removeModel = (id: number) =>
    setForm({ ...form, models: form.models.length > 1 ? form.models.filter((m) => m.id !== id) : form.models })

  const testModel = async (row: ModelRow) => {
    const name = row.name.trim()
    if (!name || row.test === 'testing') return
    patchModel(row.id, { test: 'testing' })
    try {
      const r = await onTest(form.base_url.trim(), form.api_key.trim(), name)
      patchModel(row.id, { test: r })
    } catch {
      patchModel(row.id, { test: { ok: false, error: t('providers.requestFailed') } })
    }
  }

  const submit = () => {
    if (!canSave) return
    onSubmit({
      id: form.id,
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
      models: validModels,
    })
  }

  return (
    <div>
      <button
        onClick={onCancel}
        className="flex items-center gap-2 text-base font-medium text-ink/90 hover:text-ink transition mb-5"
      >
        <ArrowLeft size={17} className="shrink-0" />
        {editing ? t('providers.editTitle') : t('providers.addTitle')}
      </button>

      <div className="space-y-4">
        <Field label={t('providers.name')} value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder={t('providers.namePlaceholder')} />
        <Field label={t('providers.baseUrl')} value={form.base_url} onChange={(v) => setForm({ ...form, base_url: v })} placeholder={t('providers.baseUrlPlaceholder')} />
        <Field label={t('providers.apiKey')} value={form.api_key} onChange={(v) => setForm({ ...form, api_key: v })} placeholder="sk-…" password />

        <div>
          <div className="text-xs text-muted-foreground mb-1.5">{t('providers.models')}</div>
          <div className="space-y-1.5">
            {form.models.map((row) => (
              <ModelRowEditor
                key={row.id}
                row={row}
                canRemove={form.models.length > 1}
                onChange={(v) => patchModel(row.id, { name: v, test: undefined })}
                onTest={() => testModel(row)}
                onRemove={() => removeModel(row.id)}
              />
            ))}
          </div>
          <Button variant="ghost" size="xs" onClick={addModel} className="mt-2 text-muted-foreground">
            <Plus />
            {t('providers.addModel')}
          </Button>
        </div>
      </div>

      <div className="flex justify-end gap-2 mt-6">
        <Button variant="ghost" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        <Button onClick={submit} disabled={!canSave}>
          {editing ? t('common.save') : t('common.add')}
        </Button>
      </div>
    </div>
  )
}

// 单个模型行：名称输入 + 测试（就地显示结果，可重测）+ (?) 费用提示 + 删除
function ModelRowEditor({
  row,
  canRemove,
  onChange,
  onTest,
  onRemove,
}: {
  row: ModelRow
  canRemove: boolean
  onChange: (v: string) => void
  onTest: () => void
  onRemove: () => void
}) {
  const { t } = useI18n()
  const r = row.test
  return (
    <div className="flex items-center gap-2">
      <input
        value={row.name}
        onChange={(e) => onChange(e.target.value)}
        placeholder={t('providers.modelPlaceholder')}
        className="flex-1 min-w-0 px-3 py-1.5 rounded-lg text-sm bg-canvas/60 text-ink border border-line/40 focus:border-primary/50 outline-none placeholder:text-muted-foreground/50"
      />

      {r === 'testing' ? (
        <span className="shrink-0 flex items-center gap-1 text-xs text-muted-foreground">
          <Loader2 size={13} className="animate-spin" />
          {t('providers.testing')}
        </span>
      ) : r && r.ok ? (
        <button onClick={onTest} className="shrink-0 flex items-center gap-1 text-xs text-success" title={t('providers.test')}>
          <Check size={13} />
          {t('providers.ok')}
        </button>
      ) : r && !r.ok ? (
        <button onClick={onTest} className="shrink-0 flex items-center gap-1 max-w-28 text-xs text-error" title={r.error}>
          <X size={13} className="shrink-0" />
          <span className="truncate">{r.error}</span>
        </button>
      ) : (
        <Button variant="outline" size="xs" onClick={onTest} disabled={!row.name.trim()} className="shrink-0">
          {t('providers.test')}
        </Button>
      )}

      {/* 费用提示：悬停 (?) 展开（Radix Tooltip，Portal 渲染不被裁剪） */}
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="shrink-0 grid place-items-center cursor-help">
            <HelpCircle size={14} className="text-muted-foreground/50 hover:text-muted-foreground" />
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-56">{t('providers.costHint')}</TooltipContent>
      </Tooltip>

      <Button
        variant="ghost"
        size="icon-xs"
        onClick={onRemove}
        disabled={!canRemove}
        aria-label={t('providers.removeModel')}
        className="shrink-0 text-muted-foreground hover:text-error"
      >
        <Trash2 />
      </Button>
    </div>
  )
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  password,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  password?: boolean
}) {
  return (
    <label className="block">
      <span className="text-xs text-muted-foreground">{label}</span>
      <input
        type={password ? 'password' : 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="mt-1 w-full px-3 py-2 rounded-lg text-sm bg-canvas/60 text-ink border border-line/40 focus:border-primary/50 outline-none placeholder:text-muted-foreground/50"
      />
    </label>
  )
}

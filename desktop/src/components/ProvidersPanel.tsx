import { useState } from 'react'
import {
  Check,
  Pencil,
  Trash2,
  Plus,
  Loader2,
  ArrowLeft,
  HelpCircle,
  X,
} from 'lucide-react'
import type { ActiveModel, ProviderProfile } from '../types'
import { useI18n } from '../i18n'
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
  profiles,
  active,
  onSwitch,
  onSave,
  onDelete,
  onTest,
}: {
  profiles: ProviderProfile[]
  active: ActiveModel
  onSwitch: (provider: string, model: string) => void
  onSave: (draft: { id?: string; name: string; base_url: string; api_key: string; models: string[] }) => void
  onDelete: (id: string) => void
  onTest: (baseUrl: string, apiKey: string, model: string) => Promise<TestResult>
}) {
  const { t } = useI18n()
  const [form, setForm] = useState<Form | null>(null) // null = 列表视图

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
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-medium">{t('providers.title')}</h3>
        <Button variant="outline" size="sm" onClick={() => setForm(emptyForm())}>
          <Plus />
          {t('common.add')}
        </Button>
      </div>

      {profiles.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted/80">{t('providers.none')}</div>
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
                  className="shrink-0 text-muted"
                >
                  <Pencil />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => onDelete(p.id)}
                  aria-label={t('common.delete')}
                  className="shrink-0 text-muted hover:text-error"
                >
                  <Trash2 />
                </Button>
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {p.models.map((m) => {
                  const on = active.provider === p.id && active.model === m
                  return (
                    <button
                      key={m}
                      onClick={() => onSwitch(p.id, m)}
                      className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-xs border transition ${
                        on
                          ? 'border-primary/50 bg-primary/10 text-primary'
                          : 'border-line/40 text-muted hover:text-ink hover:bg-canvas/60'
                      }`}
                      title={on ? t('providers.inUse') : t('providers.switchHint')}
                    >
                      {on && <Check size={12} />}
                      {m}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
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
          <div className="text-xs text-muted mb-1.5">{t('providers.models')}</div>
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
          <Button variant="ghost" size="xs" onClick={addModel} className="mt-2 text-muted">
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
        className="flex-1 min-w-0 px-3 py-1.5 rounded-lg text-sm bg-canvas/60 text-ink border border-line/40 focus:border-primary/50 outline-none placeholder:text-muted/50"
      />

      {r === 'testing' ? (
        <span className="shrink-0 flex items-center gap-1 text-xs text-muted">
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
            <HelpCircle size={14} className="text-muted/50 hover:text-muted" />
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
        className="shrink-0 text-muted hover:text-error"
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
      <span className="text-xs text-muted">{label}</span>
      <input
        type={password ? 'password' : 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="mt-1 w-full px-3 py-2 rounded-lg text-sm bg-canvas/60 text-ink border border-line/40 focus:border-primary/50 outline-none placeholder:text-muted/50"
      />
    </label>
  )
}

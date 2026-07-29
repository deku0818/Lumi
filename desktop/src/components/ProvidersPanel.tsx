import { useCallback, useEffect, useState } from 'react'
import {
  Boxes,
  Check,
  Pencil,
  Trash2,
  Plus,
  Loader2,
  HelpCircle,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import type { ActiveModel, ModelLimits, ModelPointer, ProviderProfile } from '../types'
import type { Gateway } from '../gateway'
import { useI18n } from '../i18n'
import { MachineScope } from './MachineTabs'
import { Empty, EntityCard, Field, FormModal, Row, Section, SectionGroup, TextInput } from './SettingsKit'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'
import { fmtTokensFull } from '@/lib/utils'

type TestResult = { ok: boolean; error?: string; latency_ms?: number }
type RowTest = 'testing' | TestResult | undefined
// ctx / out = 用户填的上下文窗口 / 单次输出上限覆盖，空串 = 不覆盖（跟随探测值）。
// 存字符串而非数字：输入框清空态与「填了 0」必须可区分，且不必在每次击键时解析。
type ModelRow = { id: number; name: string; test: RowTest; ctx: string; out: string }
type Form = { id?: string; name: string; base_url: string; api_key: string; models: ModelRow[] }

let _rid = 0
const newId = () => ++_rid
const newRow = (name = ''): ModelRow => ({ id: newId(), name, test: undefined, ctx: '', out: '' })
// 0 = 后端表示「没配 / 没探测到」，UI 一律呈现为空
const numText = (n: number | undefined) => (n ? String(n) : '')

const emptyForm = (): Form => ({
  name: '',
  base_url: '',
  api_key: '',
  models: [newRow()],
})

const formFrom = (p: ProviderProfile): Form => ({
  id: p.id,
  name: p.name,
  base_url: p.base_url,
  api_key: p.api_key,
  models: (p.models.length ? p.models : ['']).map((m) => ({
    ...newRow(m),
    ctx: numText(p.context?.[m]),
    out: numText(p.max_tokens?.[m]),
  })),
})

// 模型提供商面板（设置 → 模型）。两个视图：
//   列表视图：右上角「添加提供商」，下方提供商卡片（模型 chip 可点切换、编辑、删除）。
//   表单视图：添加/编辑——一套连接 + 逐行模型（每行带「测试」与费用提示）。
export function ProvidersPanel({
  gwFor,
  onChanged,
}: {
  gwFor: (id: string) => Gateway | undefined
  onChanged: (machine: string) => void
}) {
  const { t } = useI18n()
  // 方案甲「先选机器」：每台机器各自持有 providers（后端 providers.json）；按机器读写。
  const [machine, setMachine] = useState('local')
  const [profiles, setProfiles] = useState<ProviderProfile[]>([])
  const [active, setActive] = useState<ActiveModel>({ provider: '', model: '' })
  const [classifier, setClassifier] = useState<ModelPointer>({})
  const [titler, setTitler] = useState<ModelPointer>({})
  // 后端兜底值（模型既无用户覆盖也没探测到时实际会用的数）；UI 显示它以免与实跑口径不一致
  const [fallback, setFallback] = useState<ModelLimits>({ context: 0, max_tokens: 0 })
  const [form, setForm] = useState<Form | null>(null) // null = 关闭 provider 表单
  const [picking, setPicking] = useState<PickTarget | null>(null) // 打开模型选择弹窗的用途

  const reload = useCallback(() => {
    gwFor(machine)
      ?.listProviders()
      .then((r) => {
        setProfiles(r.profiles ?? [])
        setActive(r.active ?? { provider: '', model: '' })
        setClassifier(r.classifier ?? {})
        setTitler(r.titler ?? {})
        setFallback(r.fallback ?? { context: 0, max_tokens: 0 })
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
    classifier?: ModelPointer
    titler?: ModelPointer
  }) => {
    setProfiles(r.profiles ?? [])
    setActive(r.active ?? { provider: '', model: '' })
    // 删/改 provider 后端会规范化清掉失效的用途指针，须同步回写避免 UI 陈旧
    setClassifier(r.classifier ?? {})
    setTitler(r.titler ?? {})
    onChanged(machine)
  }
  // 三处「模型用途」的直接设值（空 provider/model = 会话模型 / 跟随会话模型）。
  // 三处「模型用途」的设值只差 gw 方法与回写目标，工厂消掉 .then/onChanged/.catch 三连的重复。
  type PickResp = { active?: ActiveModel; classifier?: ModelPointer; titler?: ModelPointer }
  const makePick =
    (run: (p: string, m: string) => Promise<PickResp> | undefined, apply: (r: PickResp) => void) =>
    (provider: string, model: string) =>
      run(provider, model)
        ?.then((r) => {
          apply(r)
          onChanged(machine)
        })
        .catch(() => {})
  const pickSession = makePick((p, m) => gw?.setProvider(p, m), (r) => setActive(r.active ?? { provider: '', model: '' }))
  const pickClassifier = makePick((p, m) => gw?.setClassifier(p, m), (r) => setClassifier(r.classifier ?? {}))
  const pickTitler = makePick((p, m) => gw?.setTitler(p, m), (r) => setTitler(r.titler ?? {}))
  const onSave = (draft: Partial<ProviderProfile>) => gw?.saveProvider(draft).then(apply).catch(() => {})
  const onDelete = (id: string) => gw?.deleteProvider(id).then(apply).catch(() => {})
  const onTest = (baseUrl: string, apiKey: string, model: string): Promise<TestResult> =>
    gw?.testProvider(baseUrl, apiKey, model) ??
    Promise.resolve({ ok: false, error: t('sidebar.disconnected') })

  // 用途行悬停提示：跨 provider 同名模型时用「provider · model」区分（无指向则不提示）
  const pointerTitle = (p: ModelPointer) => {
    if (!p.provider) return undefined
    const name = profiles.find((x) => x.id === p.provider)?.name ?? p.provider
    return `${name} · ${p.model}`
  }

  // 三行「模型用途」的数据表：会话模型无「跟随」项（allowFollow=false），标题/分类器有。
  const usages: Array<{
    label: string
    hint: string
    pointer: ModelPointer
    fallback: string
    allowFollow: boolean
    onPick: (provider: string, model: string) => void
  }> = [
    { label: t('providers.sessionModel'), hint: t('providers.sessionModelHint'), pointer: active, fallback: t('providers.pickNone'), allowFollow: false, onPick: pickSession },
    { label: t('titler.title'), hint: t('titler.desc'), pointer: titler, fallback: t('pointer.follow'), allowFollow: true, onPick: pickTitler },
    { label: t('classifier.title'), hint: t('classifier.desc'), pointer: classifier, fallback: t('pointer.follow'), allowFollow: true, onPick: pickClassifier },
  ]

  return (
    <div>
      <MachineScope value={machine} onChange={setMachine}>
      <SectionGroup>
      <Section
        title={t('providers.title')}
        action={
          <Button variant="outline" size="sm" onClick={() => setForm(emptyForm())}>
            <Plus />
            {t('common.add')}
          </Button>
        }
      >
        {profiles.length === 0 ? (
          <Empty>{t('providers.none')}</Empty>
        ) : (
          <div className="space-y-2">
            {profiles.map((p) => (
              <EntityCard
                key={p.id}
                icon={<Boxes size={17} />}
                title={p.name}
                subtitle={
                  <span className="font-mono">
                    {p.base_url} · {t('providers.modelCount', { n: p.models.length })}
                  </span>
                }
                subtitleTitle={p.base_url}
                actions={
                  <>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => setForm(formFrom(p))}
                      aria-label={t('providers.edit')}
                      className="text-muted-foreground"
                    >
                      <Pencil />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => onDelete(p.id)}
                      aria-label={t('common.delete')}
                      className="text-muted-foreground hover:text-error"
                    >
                      <Trash2 />
                    </Button>
                  </>
                }
              />
            ))}
          </div>
        )}
      </Section>

      {/* 模型用途：会话模型 / 标题 / 分类器三处都是「当前值 + 更改」，点更改弹出选择框。无 provider 不渲染 */}
      {profiles.length > 0 && (
        <Section title={t('providers.usage')}>
          {usages.map((u) => (
            <UsageRow
              key={u.label}
              label={u.label}
              hint={u.hint}
              value={u.pointer.model || u.fallback}
              valueTitle={pointerTitle(u.pointer)}
              muted={!u.pointer.model}
              onChange={() =>
                setPicking({ title: u.label, current: u.pointer, allowFollow: u.allowFollow, onPick: u.onPick })
              }
            />
          ))}
        </Section>
      )}
      </SectionGroup>
      </MachineScope>

      {form && (
        <ProviderForm
          initial={form}
          probe={profiles.find((p) => p.id === form.id)?.probe ?? {}}
          fallback={fallback}
          onTest={onTest}
          onSubmit={(draft) => {
            onSave(draft)
            setForm(null)
          }}
          onCancel={() => setForm(null)}
        />
      )}

      {picking && (
        <ModelPickerModal
          target={picking}
          profiles={profiles}
          onPick={(provider, model) => {
            picking.onPick(provider, model)
            setPicking(null)
          }}
          onClose={() => setPicking(null)}
        />
      )}
    </div>
  )
}

// 一处「模型用途」的当前值 + 状态；点「更改」打开 ModelPickerModal。
type PickTarget = {
  title: string
  current: ModelPointer
  allowFollow: boolean
  onPick: (provider: string, model: string) => void
}

// 一行「模型用途」：label/说明在左，当前值 + 更改在右。
// valueTitle：悬停显示「provider · model」，用于区分跨 provider 的同名模型。
function UsageRow({
  label,
  hint,
  value,
  valueTitle,
  muted,
  onChange,
}: {
  label: string
  hint: string
  value: string
  valueTitle?: string
  muted: boolean
  onChange: () => void
}) {
  const { t } = useI18n()
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2.5">
        <span
          title={valueTitle}
          className={`max-w-40 truncate text-xs ${muted ? 'text-muted-foreground' : 'text-ink'}`}
        >
          {value}
        </span>
        <Button variant="outline" size="sm" onClick={onChange}>
          {t('common.change')}
        </Button>
      </div>
    </Row>
  )
}

// 模型选择弹窗：搜索 + 按 provider 分组的模型列表；用途指针（标题/分类器）含「跟随会话模型」。
// 选中即回调并关闭；会话模型无「跟随」项（allowFollow=false）。
function ModelPickerModal({
  target,
  profiles,
  onPick,
  onClose,
}: {
  target: PickTarget
  profiles: ProviderProfile[]
  onPick: (provider: string, model: string) => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const [q, setQ] = useState('')
  const ql = q.trim().toLowerCase()
  const following = !target.current.provider

  return (
    <FormModal onClose={onClose} title={target.title} className="sm:max-w-sm">
      <TextInput
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder={t('providers.searchModel')}
        className="mb-2.5"
      />

      {target.allowFollow && (
        <button
          onClick={() => onPick('', '')}
          className={`mb-1.5 flex w-full items-center gap-2 rounded-xl border border-dashed px-3 py-2.5 text-left transition ${
            following ? 'border-primary/40 bg-primary/5' : 'border-line/50 hover:bg-canvas/60'
          }`}
        >
          <span className={following ? 'lumi-orb lumi-orb-idle shrink-0' : 'size-2 shrink-0 rounded-full bg-muted-foreground/40'} />
          <span className="min-w-0 flex-1">
            <span className="block text-[13px]">{t('pointer.follow')}</span>
            <span className="block text-[11px] text-muted-foreground">{t('pointer.followHint')}</span>
          </span>
          {following && <Check size={14} className="shrink-0 text-primary" />}
        </button>
      )}

      <div className="space-y-0.5">
        {profiles.map((p) => {
          const models = p.models.filter((m) => m.toLowerCase().includes(ql))
          if (!models.length) return null
          return (
            <div key={p.id}>
              <div className="px-1 pt-2.5 pb-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
                {p.name}
              </div>
              {models.map((m) => {
                const on = target.current.provider === p.id && target.current.model === m
                return (
                  <button
                    key={m}
                    onClick={() => onPick(p.id, m)}
                    className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] transition hover:bg-line/30 ${
                      on ? 'text-primary' : 'text-ink'
                    }`}
                  >
                    <Check size={14} className={`shrink-0 ${on ? 'text-primary' : 'opacity-0'}`} />
                    <span className="min-w-0 truncate">{m}</span>
                  </button>
                )
              })}
            </div>
          )
        })}
      </div>
    </FormModal>
  )
}

function ProviderForm({
  initial,
  probe,
  fallback,
  onTest,
  onSubmit,
  onCancel,
}: {
  initial: Form
  probe: Record<string, ModelLimits>
  fallback: ModelLimits
  onTest: (baseUrl: string, apiKey: string, model: string) => Promise<TestResult>
  onSubmit: (draft: {
    id?: string
    name: string
    base_url: string
    api_key: string
    models: string[]
    context: Record<string, number>
    max_tokens: Record<string, number>
  }) => void
  onCancel: () => void
}) {
  const { t } = useI18n()
  // 草稿存本组件本地（与 FeishuForm/RemoteForm 一致）：键入不再 setState 到父级、避免整页重渲染。
  const [form, setForm] = useState<Form>(initial)
  const editing = form.id != null
  // 去重：同名模型会在选择弹窗里造成重复 key + 双高亮，保存时按名去重
  const validModels = [...new Set(form.models.map((m) => m.name.trim()).filter(Boolean))]
  // 提供商名称、Base URL（必填）、至少一个模型
  const canSave = !!form.name.trim() && !!form.base_url.trim() && validModels.length > 0

  const patchModel = (id: number, patch: Partial<ModelRow>) =>
    setForm({ ...form, models: form.models.map((m) => (m.id === id ? { ...m, ...patch } : m)) })
  const addModel = () => setForm({ ...form, models: [...form.models, newRow()] })
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

  // 限制覆盖表：只收正整数，其余（空 / 0 / 乱填）一律不进表 = 该模型跟随探测值。
  // 必须就地取整：后端 int() 会把 128.9 截成 128，一个 128 token 的窗口能让压缩每轮触发
  const limitMap = (pick: (r: ModelRow) => string) =>
    Object.fromEntries(
      form.models
        .map((r) => [r.name.trim(), Math.round(Number(pick(r).trim()))] as const)
        .filter(([name, n]) => name && Number.isFinite(n) && n > 0),
    )

  const submit = () => {
    if (!canSave) return
    onSubmit({
      id: form.id,
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
      models: validModels,
      context: limitMap((r) => r.ctx),
      max_tokens: limitMap((r) => r.out),
    })
  }

  const footer = (
    <>
      <div className="flex-1" />
      <Button variant="ghost" onClick={onCancel}>
        {t('common.cancel')}
      </Button>
      <Button onClick={submit} disabled={!canSave}>
        {editing ? t('common.save') : t('common.add')}
      </Button>
    </>
  )

  return (
    <FormModal
      onClose={onCancel}
      title={editing ? t('providers.editTitle') : t('providers.addTitle')}
      footer={footer}
    >
      <div className="space-y-4">
        <Field label={t('providers.name')}>
          <TextInput value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={t('providers.namePlaceholder')} />
        </Field>
        <Field label={t('providers.baseUrl')}>
          <TextInput value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder={t('providers.baseUrlPlaceholder')} />
        </Field>
        <Field label={t('providers.apiKey')}>
          <TextInput password value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder="sk-…" />
        </Field>

        <div>
          <div className="text-xs text-muted-foreground mb-1.5">{t('providers.models')}</div>
          <div className="space-y-1.5">
            {form.models.map((row) => (
              <ModelRowEditor
                key={row.id}
                row={row}
                canRemove={form.models.length > 1}
                probe={probe[row.name.trim()]}
                fallback={fallback}
                onChange={(v) => patchModel(row.id, { name: v, test: undefined })}
                onPatch={(patch) => patchModel(row.id, patch)}
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
    </FormModal>
  )
}

// 单个模型行：名称输入 + 测试（就地显示结果，可重测）+ (?) 费用提示 + 限制展开 + 删除
function ModelRowEditor({
  row,
  canRemove,
  probe,
  fallback,
  onChange,
  onPatch,
  onTest,
  onRemove,
}: {
  row: ModelRow
  canRemove: boolean
  probe?: ModelLimits
  fallback: ModelLimits
  onChange: (v: string) => void
  onPatch: (patch: Partial<ModelRow>) => void
  onTest: () => void
  onRemove: () => void
}) {
  const { t } = useI18n()
  const r = row.test
  const overridden = !!row.ctx.trim() || !!row.out.trim()
  // 有覆盖值时默认展开：藏起用户自己配过的数值比省一行空间更糟
  const [open, setOpen] = useState(overridden)
  return (
    <div>
      <div className="flex items-center gap-2">
        <TextInput
          value={row.name}
          onChange={(e) => onChange(e.target.value)}
          placeholder={t('providers.modelPlaceholder')}
          className="flex-1 min-w-0 h-8"
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
          onClick={() => setOpen(!open)}
          aria-label={t('providers.limitsTitle')}
          title={t('providers.limitsTitle')}
          className={`shrink-0 ${open || overridden ? 'text-primary' : 'text-muted-foreground'}`}
        >
          <SlidersHorizontal />
        </Button>

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
      {open && (
        <div className="mt-1 grid grid-cols-2 gap-2.5 rounded-lg border border-line/40 bg-canvas/30 px-3 py-2.5">
          <LimitField
            label={t('providers.contextWindow')}
            value={row.ctx}
            probe={probe?.context}
            fallback={fallback.context}
            warn={t('providers.contextWarn')}
            onChange={(v) => onPatch({ ctx: v })}
          />
          <LimitField
            label={t('providers.maxTokens')}
            value={row.out}
            probe={probe?.max_tokens}
            fallback={fallback.max_tokens}
            warn={t('providers.maxTokensWarn')}
            onChange={(v) => onPatch({ out: v })}
          />
        </div>
      )}
    </div>
  )
}

// 单个限制输入：占位符即探测值（未探测到则显示兜底值），下方一行说明当前取的是谁的值。
// 填得比探测值大时就地警示——猜高了上下文会该压不压撞 PTL、输出上限会被服务端 400。
// probe 语义三态：undefined = 模型还没保存过（后端未探测）、0 = 探测过但目录里没有、>0 = 探测值。
function LimitField({
  label,
  value,
  probe,
  fallback,
  warn,
  onChange,
}: {
  label: string
  value: string
  probe?: number
  fallback: number
  warn: string
  onChange: (v: string) => void
}) {
  const { t } = useI18n()
  const n = Number(value.trim())
  const set = !!value.trim() && Number.isFinite(n) && n > 0
  const over = set && !!probe && n > probe
  // 状态行的文案与色调同源：分开写会在新增状态时漏改其中一半。按 已填 → 未保存 →
  // 探测到 → 探测不到 的顺序，与 probe 的三态声明顺序一致。
  const hint = set
    ? { text: t('providers.limitSet'), missing: false }
    : probe === undefined
      ? { text: t('providers.limitPending'), missing: false }
      : probe
        ? { text: t('providers.limitProbe', { n: fmtTokensFull(probe) }), missing: false }
        : { text: t('providers.limitFallback', { n: fmtTokensFull(fallback) }), missing: true }
  return (
    <Field
      label={label}
      hint={
        <>
          <span className={hint.missing ? 'text-error/85' : undefined}>{hint.text}</span>
          {over && <div className="mt-1 text-primary">{warn}</div>}
        </>
      }
    >
      <TextInput
        type="number"
        min={1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={fmtTokensFull(probe || fallback)}
        className={`h-8 text-right tabular-nums ${set ? 'border-primary/45' : ''}`}
      />
    </Field>
  )
}

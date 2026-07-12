import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Check,
  ChevronDown,
  ChevronRight,
  Folder,
  FolderPlus,
  Globe,
  Pencil,
  Plus,
  Radar,
  Terminal,
  Trash2,
  X,
} from 'lucide-react'
import type {
  McpPromptInfo,
  McpResourceInfo,
  McpScope,
  McpServerConfig,
  McpServers,
  McpServerStatus,
  McpTestResult,
  McpTransport,
  Project,
} from '../types'
import type { Gateway } from '../gateway'
import { MachineTabs } from './MachineTabs'
import { DirBrowser } from './DirBrowser'
import { basename } from '@/lib/utils'
import { Section, Card, Field, TextInput, SegmentedControl, FormModal } from './SettingsKit'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

// —— 配置 ↔ 视图的纯函数（与 .demos/mcp-panel.html 同源）——
const isStdio = (t: McpTransport) => t === 'stdio'
const transportOf = (s: McpServerConfig): McpTransport =>
  s.transport ?? (s.url ? 'streamable_http' : 'stdio')
const tagLabel = (t: McpTransport) => (isStdio(t) ? 'stdio' : t === 'sse' ? 'SSE' : 'HTTP')
const subOf = (s: McpServerConfig) =>
  isStdio(transportOf(s)) ? [s.command, ...(s.args ?? [])].filter(Boolean).join(' ') : (s.url ?? '')

// 传输类型胶囊（server 卡片 / 测试弹窗标题共用）
function TransportTag({ config }: { config: McpServerConfig }) {
  return (
    <span className="text-[10px] font-medium uppercase tracking-wide px-1.5 py-px rounded-full border border-separator text-muted-foreground">
      {tagLabel(transportOf(config))}
    </span>
  )
}

// MCP 管理面板（设置 → MCP）。两个维度：机器（MachineTabs）· 作用范围（全局/项目）。
// 列表：server 卡片（传输 tag + 命令/URL 摘要 + 启用开关 + 测试/编辑/删除）。
// 编辑：表单 / JSON 双模式。禁用 = 存 disabled:true（加载侧剥离，不下传 adapter）。
export function McpPanel({
  machines,
  gwFor,
}: {
  machines: { id: string; name: string }[]
  gwFor: (id: string) => Gateway | undefined
}) {
  const [machine, setMachine] = useState('local')
  const [scope, setScope] = useState<McpScope>('global')
  const [project, setProject] = useState('') // 项目作用范围下选中的项目路径
  const [servers, setServers] = useState<McpServers>({})
  const [status, setStatus] = useState<Record<string, McpServerStatus>>({})
  const [poolLoading, setPoolLoading] = useState(false)
  const [editing, setEditing] = useState<string | null | undefined>(undefined) // undefined=列表；null=新增；string=编辑该 server
  const [testing, setTesting] = useState<string | null>(null) // 连接测试弹窗打开的 server 名

  const gw = gwFor(machine)
  const inProject = scope === 'project'
  // 项目范围但未选项目时不请求（无目标文件）
  const ready = !inProject || !!project

  // 会话池最近加载状态（徽标数据源）：项目 scope 查该项目池，global 查全局池。
  // 独立于 reload——loading 轮询只需对账状态，不必重拉配置列表
  const fetchStatus = useCallback(() => {
    gwFor(machine)
      ?.getMcpStatus(inProject ? project : '')
      .then((r) => {
        setStatus(Object.fromEntries(r.servers.map((s) => [s.name, s])))
        setPoolLoading(r.loading)
      })
      .catch(() => {
        setStatus({})
        setPoolLoading(false)
      })
  }, [gwFor, machine, project, inProject])

  const reload = useCallback(() => {
    if (inProject && !project) {
      setServers({})
      return
    }
    gwFor(machine)
      ?.listMcpServers(scope, inProject ? project : '')
      .then((r) => setServers(r.servers ?? {}))
      .catch(() => setServers({}))
    fetchStatus()
  }, [gwFor, machine, scope, project, inProject, fetchStatus])

  // 切机器时重置项目选择（各机器项目集不同）。在渲染中调整而非 effect：
  // 否则 reload 会先用「新机器 + 旧机器的项目路径」错配打一次请求，再被重置触发第二次。
  const [prevMachine, setPrevMachine] = useState(machine)
  if (machine !== prevMachine) {
    setPrevMachine(machine)
    setProject('')
  }

  useEffect(() => {
    reload()
  }, [reload])

  // 池后台加载完成的进程级广播（App 转发为 window 信号）：面板开着时即时刷徽标
  useEffect(() => {
    const h = () => reload()
    window.addEventListener('lumi:mcp-status', h)
    return () => window.removeEventListener('lumi:mcp-status', h)
  }, [reload])

  // 加载中轮询兜底：mcp.status 只发给绑定该池的连接，面板浏览的项目可能没有
  // 绑定连接（会话已关/从未打开），完成事件送达不了——loading 期间每 3s 对账状态，
  // 否则徽标会永远停在「正在后台连接…」
  useEffect(() => {
    if (!poolLoading) return
    const t = setInterval(fetchStatus, 3000)
    return () => clearInterval(t)
  }, [poolLoading, fetchStatus])

  const save = (name: string, config: McpServerConfig, originalName?: string) =>
    gw
      ?.saveMcpServer(scope, project, name, config)
      .then(async (r) => {
        // 改名：save 只写新键，旧键要单独删。删失败则 reload 回真实态（旧+新并存），
        // 不吞错、不误报成功——弹窗保持打开让用户重试/手动清理。
        if (originalName && originalName !== name) {
          const r2 = await gw.deleteMcpServer(scope, project, originalName)
          setServers(r2.servers ?? {})
        } else {
          setServers(r.servers ?? {})
        }
        setEditing(undefined)
      })
      .catch(() => reload())

  const remove = (name: string) =>
    gw
      ?.deleteMcpServer(scope, project, name)
      .then((r) => setServers(r.servers ?? {}))
      .catch(() => reload())

  // 开关：翻转 disabled 立即保存（其余字段不动）
  const toggle = (name: string, on: boolean) => {
    const { disabled: _drop, ...rest } = servers[name]
    save(name, on ? rest : { ...rest, disabled: true })
  }

  const path = inProject
    ? `${project || '<项目>'}/.lumi/mcp_server.json`
    : '~/.lumi/mcp_server.json'
  const names = Object.keys(servers)

  return (
    <div>
      <MachineTabs machines={machines} value={machine} onChange={setMachine} />

      {/* 作用范围行 */}
      <div className="flex items-center gap-3 mb-4 px-3 py-2.5 rounded-xl border border-line/50 bg-surface/40">
        <span className="text-xs text-muted-foreground shrink-0">作用范围</span>
        <SegmentedControl
          className="shrink-0"
          value={scope}
          onChange={(v) => setScope(v as McpScope)}
          options={[
            { val: 'global', label: '全局' },
            { val: 'project', label: '项目' },
          ]}
        />
        {inProject && <ProjectSelect gw={gw} value={project} onChange={setProject} />}
        <span className="ml-auto min-w-0 truncate font-mono text-[10.5px] text-muted-foreground">
          {path}
        </span>
      </div>

      <Section
        title="MCP 服务器"
        action={
          ready && (
            <Button variant="outline" size="sm" onClick={() => setEditing(null)}>
              <Plus size={14} className="mr-1" />
              添加
            </Button>
          )
        }
      >
        {!ready ? (
          <Empty>选择一个项目以管理其专属 MCP 服务器。</Empty>
        ) : names.length === 0 ? (
          <Empty>
            {inProject ? (
              <>
                该项目还没有专属 MCP 服务器。
                <br />
                它仍会加载全局层的 server；在此「添加」的只对本项目生效。
              </>
            ) : (
              <>还没有 MCP 服务器。点右上「添加」接入第一个。</>
            )}
          </Empty>
        ) : (
          <div className="space-y-2">
            {names.map((name) => (
              <ServerCard
                key={name}
                name={name}
                config={servers[name]}
                status={status[name]}
                poolLoading={poolLoading}
                onToggle={(on) => toggle(name, on)}
                onTest={() => setTesting(name)}
                onEdit={() => setEditing(name)}
                onDelete={() => remove(name)}
              />
            ))}
          </div>
        )}
      </Section>

      {testing !== null && servers[testing] && (
        <TestDialog
          gw={gw}
          name={testing}
          config={servers[testing]}
          onClose={() => setTesting(null)}
        />
      )}

      {editing !== undefined && (
        <ServerForm
          name={editing}
          config={editing ? servers[editing] : undefined}
          existing={names}
          onCancel={() => setEditing(undefined)}
          onSave={save}
          onDelete={editing ? () => remove(editing) : undefined}
        />
      )}
    </div>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-separator px-5 py-7 text-center text-[12px] leading-relaxed text-muted-foreground">
      {children}
    </div>
  )
}

function ServerCard({
  name,
  config,
  status,
  poolLoading,
  onToggle,
  onTest,
  onEdit,
  onDelete,
}: {
  name: string
  config: McpServerConfig
  status?: McpServerStatus // 会话池最近一次加载该 server 的结果（无记录 = 池未加载/未含它）
  poolLoading?: boolean
  onToggle: (on: boolean) => void
  onTest: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  const t = transportOf(config)
  const off = config.disabled === true
  // 状态徽标：绿=已连接（title 显示工具数）、红=失败（title 显示原因）、
  // 灰呼吸=池后台加载中；池未加载过则不显示（避免误导为"离线"）
  const dot = status ? (
    <span
      title={status.ok ? `已连接 · ${status.tools ?? 0} 个工具` : status.error}
      className={`size-1.5 rounded-full shrink-0 ${status.ok ? 'bg-success' : 'bg-error'}`}
    />
  ) : poolLoading && !off ? (
    <span title="正在后台连接…" className="size-1.5 rounded-full shrink-0 bg-separator animate-pulse" />
  ) : null
  return (
    <Card className={`flex items-center gap-3 ${off ? 'opacity-55' : ''}`}>
      <div className="grid place-items-center w-9 h-9 rounded-lg bg-surface border border-line text-ink shrink-0">
        {isStdio(t) ? <Terminal size={17} /> : <Globe size={17} />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-medium flex items-center gap-2">
          {name}
          <TransportTag config={config} />
          {dot}
        </div>
        <div className="text-[11px] mt-0.5 truncate font-mono text-muted-foreground">
          {subOf(config)}
        </div>
      </div>
      <Switch checked={!off} onCheckedChange={onToggle} />
      <Button
        variant="ghost"
        size="icon"
        onClick={onTest}
        title="测试连接"
        className="text-muted-foreground h-8 w-8"
      >
        <Radar size={15} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        onClick={onEdit}
        className="text-muted-foreground h-8 w-8"
      >
        <Pencil size={15} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        onClick={onDelete}
        className="text-muted-foreground hover:text-error h-8 w-8"
      >
        <Trash2 size={15} />
      </Button>
    </Card>
  )
}

// —— 连接测试弹窗：用当前配置临时连一次 server，握手成功后浏览工具/提示/资源 ——

// 参数树节点：嵌套 object 的子字段挂在 children 上，UI 里点「N 个字段」下钻
type ParamNode = {
  name: string
  type: string
  required: boolean
  description: string
  children: ParamNode[]
}

type Schema = Record<string, unknown>

// 解包 $ref（#/$defs/X，Pydantic 嵌套模型的标准形态）与单元素 allOf 包装；
// $ref 同级字段（如 description）覆盖目标 schema 的同名字段
function deref(p: Schema, root: Schema): Schema {
  if (typeof p.$ref === 'string' && p.$ref.startsWith('#/')) {
    let node: unknown = root
    for (const seg of p.$ref.slice(2).split('/')) node = (node as Schema | undefined)?.[seg]
    if (node && typeof node === 'object') {
      const { $ref: _drop, ...rest } = p
      return { ...(node as Schema), ...rest }
    }
  }
  const allOf = p.allOf as Schema[] | undefined
  if (allOf?.length === 1) {
    const { allOf: _drop, ...rest } = p
    return { ...deref(allOf[0], root), ...rest }
  }
  return p
}

// 递归深度封顶：既是 UI 下钻层数上限，也拦住递归模型（$ref 自引用环）的无限遍历
const MAX_DEPTH = 5

// JSON Schema 属性 → 展示用类型名（anyOf/oneOf 拍平为 a | b，数组显示为 T[]）
function schemaType(p: Schema, root: Schema, depth = 0): string {
  if (depth >= MAX_DEPTH) return ''
  if (p.type === 'array') {
    const item = p.items ? schemaType(deref(p.items as Schema, root), root, depth + 1) : ''
    return item ? `${item}[]` : 'array'
  }
  if (typeof p.type === 'string') return p.type
  if (Array.isArray(p.type)) return p.type.join(' | ')
  const variants = (p.anyOf ?? p.oneOf) as Schema[] | undefined
  if (variants)
    return [
      ...new Set(variants.map((v) => schemaType(deref(v, root), root, depth + 1)).filter(Boolean)),
    ].join(' | ')
  if (p.properties) return 'object'
  return ''
}

// 属性里可继续下钻的子对象 schema：自身是 object / 数组元素是 object / anyOf 变体里藏着 object
function nestedSchema(p: Schema, root: Schema, depth = 0): Schema | null {
  if (depth >= MAX_DEPTH) return null
  if (p.properties) return p
  const items = p.items as Schema | undefined
  if (items) {
    const d = deref(items, root)
    if (d.properties) return d
  }
  const variants = (p.anyOf ?? p.oneOf) as Schema[] | undefined
  for (const v of variants ?? []) {
    const n = nestedSchema(deref(v, root), root, depth + 1)
    if (n) return n
  }
  return null
}

// 递归构建参数树（如 repair_plan → context / language）
function toolParams(schema?: Schema, root: Schema = schema ?? {}, depth = 0): ParamNode[] {
  const props = (schema?.properties ?? {}) as Record<string, Schema>
  const required = new Set((schema?.required as string[]) ?? [])
  return Object.entries(props).map(([n, raw]) => {
    const p = deref(raw, root)
    const child = depth < MAX_DEPTH ? nestedSchema(p, root) : null
    return {
      name: n,
      type: schemaType(p, root),
      required: required.has(n),
      description: typeof p.description === 'string' ? p.description : '',
      children: child ? toolParams(child, root, depth + 1) : [],
    }
  })
}

// 单个参数：名称 + 类型胶囊 + 必填/可选；嵌套 object 带「N 个字段」胶囊，点开下钻子框
function ParamItem({ p }: { p: ParamNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="py-2">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-xs font-medium text-ink">{p.name}</span>
        {p.type && (
          <span className="rounded-[5px] bg-info/10 px-1.5 py-px font-mono text-[10.5px] text-info">
            {p.type}
          </span>
        )}
        <span className={`text-[10px] ${p.required ? 'text-primary' : 'text-muted-foreground'}`}>
          {p.required ? '必填' : '可选'}
        </span>
        {p.children.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="inline-flex items-center gap-1 rounded-full border border-line bg-panel px-2 py-px text-[10.5px] text-muted-foreground hover:border-separator hover:text-ink"
          >
            <ChevronRight size={9} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
            {p.children.length} 个字段
          </button>
        )}
      </div>
      {p.description && (
        <div className="mt-0.5 text-[11.5px] leading-relaxed text-muted-foreground">
          {p.description}
        </div>
      )}
      {open && (
        <div className="mt-2 divide-y divide-line/40 rounded-lg border border-line/70 bg-surface/55 px-3">
          {p.children.map((c) => (
            <ParamItem key={c.name} p={c} />
          ))}
        </div>
      )}
    </div>
  )
}

// 能力条目（工具/提示共用）：名称行 + 描述恒在第二行（收起单行截断、展开放开全文），
// 点击行仅展开/收起参数框；收起时每条高度一致
function CapItem({
  name,
  description,
  params,
}: {
  name: string
  description: string
  params: ParamNode[]
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`rounded-lg ${open ? 'border border-line/60 bg-surface/40' : ''}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-surface/60"
      >
        <div className="flex items-center gap-2">
          <ChevronRight
            size={12}
            className={`shrink-0 text-muted-foreground transition-transform ${open ? 'rotate-90' : ''}`}
          />
          <span className="min-w-0 truncate font-mono text-xs text-ink">{name}</span>
          <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
            {params.length > 0 ? `${params.length} 个参数` : '无参数'}
          </span>
        </div>
        <div
          className={`mt-0.5 ml-5 min-h-[1.6em] text-[11.5px] leading-relaxed text-muted-foreground ${open ? '' : 'truncate'}`}
        >
          {description}
        </div>
      </button>
      {open && params.length > 0 && (
        <div className="mx-2.5 mb-2.5 ml-[30px] divide-y divide-line/50 rounded-lg border border-line/80 bg-panel/90 px-3">
          {params.map((p) => (
            <ParamItem key={p.name} p={p} />
          ))}
        </div>
      )}
    </div>
  )
}

type TestTab = 'tools' | 'prompts' | 'resources'

function TestDialog({
  gw,
  name,
  config,
  onClose,
}: {
  gw?: Gateway
  name: string
  config: McpServerConfig
  onClose: () => void
}) {
  const [result, setResult] = useState<McpTestResult | null>(null) // null=连接中
  const [tab, setTab] = useState<TestTab>('tools')
  const [query, setQuery] = useState('')

  useEffect(() => {
    if (!gw) {
      setResult({ ok: false, error: '未连接到该机器' })
      return
    }
    let alive = true
    gw.testMcpServer(config)
      .then((r) => alive && setResult(r))
      // RPC 错误帧 reject 的是裸 {message} 对象，String() 会成 [object Object]
      .catch((e) => alive && setResult({ ok: false, error: String((e as Error)?.message ?? e) }))
    return () => {
      alive = false
    }
  }, [gw, config])

  // 参数树在结果到达时解析一次；过滤时不再逐键重算
  const toolItems = useMemo(
    () => (result?.tools ?? []).map((t) => ({ ...t, params: toolParams(t.input_schema) })),
    [result],
  )

  const q = query.trim().toLowerCase()
  const match = (s: string) => s.toLowerCase().includes(q)
  const tools = toolItems.filter((t) => match(t.name + t.description))
  const prompts = (result?.prompts ?? []).filter((p) => match(p.name + p.description))
  const resources = (result?.resources ?? []).filter((r) => match(r.uri + r.name + r.description))

  const tabs: { val: TestTab; label: string; count: number }[] = [
    { val: 'tools', label: '工具', count: result?.tools?.length ?? 0 },
    { val: 'prompts', label: '提示', count: result?.prompts?.length ?? 0 },
    { val: 'resources', label: '资源', count: result?.resources?.length ?? 0 },
  ]
  const active = { tools, prompts, resources }[tab] // 当前 tab 的过滤后列表（空态判断用）

  return (
    <FormModal
      onClose={onClose}
      className="sm:max-w-3xl"
      // 固定高度：切 tab / 展开条目时弹窗尺寸不变，仅列表区内部滚动
      bodyClassName="flex h-[72vh] flex-col"
      title={
        <span className="inline-flex items-center gap-2">
          {name}
          <TransportTag config={config} />
        </span>
      }
    >
      {/* 状态行：连接中（lumi-orb）→ 成功（server 信息 + 耗时）/ 失败（错误详情） */}
      <div className="flex min-h-5 shrink-0 items-center gap-2 text-xs">
        {result === null ? (
          <>
            <span className="lumi-orb" style={{ width: 11, height: 11 }} />
            <span className="text-muted-foreground">正在连接…</span>
          </>
        ) : result.ok ? (
          <>
            <span className="size-2 rounded-full bg-success shadow-[0_0_6px_var(--color-success)]" />
            <span className="text-success">已连接</span>
            <span className="font-mono text-[11px] text-muted-foreground">
              {result.server?.name} v{result.server?.version} · {result.latency_ms}ms
            </span>
          </>
        ) : (
          <>
            <span className="size-2 rounded-full bg-error" />
            <span className="text-error">连接失败</span>
          </>
        )}
      </div>

      {result !== null && !result.ok && (
        <div className="mt-3 max-h-40 overflow-auto rounded-lg border border-error/25 bg-error/10 px-3 py-2 font-mono text-[11px] leading-relaxed text-error">
          {result.error}
        </div>
      )}

      {result?.ok && (
        <>
          <div className="mt-3 flex shrink-0 gap-0.5 border-b border-line">
            {tabs.map((t) => (
              <button
                key={t.val}
                type="button"
                onClick={() => setTab(t.val)}
                className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-1.5 text-xs font-medium ${
                  tab === t.val
                    ? 'border-primary text-ink'
                    : 'border-transparent text-muted-foreground hover:text-ink'
                }`}
              >
                {t.label}
                <span
                  className={`rounded-full border px-1.5 text-[10.5px] ${
                    tab === t.val ? 'border-primary/50 text-primary' : 'border-line'
                  }`}
                >
                  {t.count}
                </span>
              </button>
            ))}
          </div>

          <TextInput
            className="mt-3 h-8 shrink-0 text-xs"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="过滤…"
          />

          <div className="mt-2 min-h-0 flex-1 space-y-0.5 overflow-y-auto">
            {tab === 'tools' &&
              tools.map((t) => (
                <CapItem key={t.name} name={t.name} description={t.description} params={t.params} />
              ))}
            {tab === 'prompts' &&
              prompts.map((p: McpPromptInfo) => (
                <CapItem
                  key={p.name}
                  name={p.name}
                  description={p.description}
                  params={p.arguments.map((a) => ({
                    name: a.name,
                    type: '',
                    required: a.required,
                    description: a.description,
                    children: [],
                  }))}
                />
              ))}
            {tab === 'resources' &&
              resources.map((r: McpResourceInfo) => (
                <div key={r.uri} className="flex items-baseline gap-2 px-2 py-1.5">
                  <span className="font-mono text-[11.5px] text-ink">{r.uri}</span>
                  {r.mime_type && (
                    <span className="rounded-full border border-line px-1.5 text-[10px] text-muted-foreground">
                      {r.mime_type}
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
                    {r.description || r.name}
                  </span>
                </div>
              ))}
            {active.length === 0 && (
              <div className="py-6 text-center text-[11.5px] text-muted-foreground">
                {query ? '没有匹配的条目' : '该 server 未提供此类能力'}
              </div>
            )}
          </div>
        </>
      )}
    </FormModal>
  )
}

// —— kv 编辑（env / headers）——
type Kv = { k: string; v: string }
const toKv = (o?: Record<string, string>): Kv[] =>
  Object.entries(o ?? {}).map(([k, v]) => ({ k, v: String(v) }))
const fromKv = (rows: Kv[]): Record<string, string> => {
  const o: Record<string, string> = {}
  for (const { k, v } of rows) if (k.trim()) o[k.trim()] = v
  return o
}

function ServerForm({
  name,
  config,
  existing,
  onCancel,
  onSave,
  onDelete,
}: {
  name: string | null
  config?: McpServerConfig
  existing: string[]
  onCancel: () => void
  onSave: (name: string, config: McpServerConfig, originalName?: string) => void
  onDelete?: () => void
}) {
  const init = config ?? { transport: 'stdio' as McpTransport }
  const originalName = name ?? undefined
  const wasDisabled = config?.disabled === true

  const [mode, setMode] = useState<'form' | 'json'>('form')
  const [srvName, setSrvName] = useState(name ?? '')
  const [transport, setTransport] = useState<McpTransport>(transportOf(init))
  const [command, setCommand] = useState(init.command ?? '')
  const [args, setArgs] = useState<string[]>(init.args ?? [])
  const [env, setEnv] = useState<Kv[]>(toKv(init.env))
  const [cwd, setCwd] = useState(init.cwd ?? '')
  const [url, setUrl] = useState(init.url ?? '')
  const [headers, setHeaders] = useState<Kv[]>(toKv(init.headers))
  const [timeout, setTimeout] = useState(init.timeout != null ? String(init.timeout) : '')
  const [sseRead, setSseRead] = useState(
    init.sse_read_timeout != null ? String(init.sse_read_timeout) : '',
  )
  const [advOpen, setAdvOpen] = useState(false)
  const [json, setJson] = useState('')
  const [jsonErr, setJsonErr] = useState('')

  const stdio = isStdio(transport)

  // 表单字段 → 配置对象（保留 disabled 元字段）
  const formToConfig = useCallback((): McpServerConfig => {
    const cfg: McpServerConfig = { transport }
    if (stdio) {
      if (command.trim()) cfg.command = command.trim()
      if (args.length) cfg.args = args
      const e = fromKv(env)
      if (Object.keys(e).length) cfg.env = e
      if (cwd.trim()) cfg.cwd = cwd.trim()
    } else {
      if (url.trim()) cfg.url = url.trim()
      const h = fromKv(headers)
      if (Object.keys(h).length) cfg.headers = h
      if (timeout.trim()) cfg.timeout = Number(timeout)
      if (sseRead.trim()) cfg.sse_read_timeout = Number(sseRead)
    }
    if (wasDisabled) cfg.disabled = true
    return cfg
  }, [transport, stdio, command, args, env, cwd, url, headers, timeout, sseRead, wasDisabled])

  // 配置对象 → 表单字段（JSON 切回表单时回填）
  const configToForm = (cfg: McpServerConfig) => {
    setTransport(transportOf(cfg))
    setCommand(cfg.command ?? '')
    setArgs(cfg.args ?? [])
    setEnv(toKv(cfg.env))
    setCwd(cfg.cwd ?? '')
    setUrl(cfg.url ?? '')
    setHeaders(toKv(cfg.headers))
    setTimeout(cfg.timeout != null ? String(cfg.timeout) : '')
    setSseRead(cfg.sse_read_timeout != null ? String(cfg.sse_read_timeout) : '')
  }

  const switchMode = (m: 'form' | 'json') => {
    setJsonErr('')
    if (m === 'json') {
      setJson(JSON.stringify(formToConfig(), null, 2))
    } else if (mode === 'json') {
      try {
        configToForm(JSON.parse(json))
      } catch (e) {
        setJsonErr('JSON 解析失败：' + (e as Error).message)
        return
      }
    }
    setMode(m)
  }

  const submit = () => {
    const n = srvName.trim()
    if (!n) return
    let cfg: McpServerConfig
    if (mode === 'json') {
      let parsed: unknown
      try {
        parsed = JSON.parse(json)
      } catch (e) {
        setJsonErr('JSON 解析失败：' + (e as Error).message)
        return
      }
      // 必须是对象——否则加载侧 _strip_disabled 会静默丢弃（数组/标量非法）
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setJsonErr('配置必须是一个 JSON 对象，如 {"command": "npx", "args": [...]}')
        return
      }
      cfg = parsed as McpServerConfig
    } else {
      cfg = formToConfig()
    }
    onSave(n, cfg, originalName)
  }

  const dup = srvName.trim() !== (name ?? '') && existing.includes(srvName.trim())

  const footer = (
    <>
      {onDelete && (
        <Button variant="ghost" onClick={onDelete} className="text-error hover:text-error">
          删除
        </Button>
      )}
      <div className="flex-1" />
      <Button variant="ghost" onClick={onCancel}>
        取消
      </Button>
      <Button onClick={submit} disabled={!srvName.trim() || dup}>
        保存
      </Button>
    </>
  )

  return (
    <FormModal
      onClose={onCancel}
      title={name ? '编辑 MCP 服务器' : '添加 MCP 服务器'}
      footer={footer}
    >
      <div className="flex justify-end mb-3">
        <SegmentedControl
          value={mode}
          onChange={(v) => switchMode(v as 'form' | 'json')}
          options={[
            { val: 'form', label: '表单' },
            { val: 'json', label: 'JSON' },
          ]}
        />
      </div>

      {mode === 'form' ? (
        <div className="space-y-4">
          <Field label="名称" hint="服务器唯一标识，作为 mcp_server.json 的键名">
            <TextInput
              value={srvName}
              onChange={(e) => setSrvName(e.target.value)}
              placeholder="my-server"
            />
            {dup && <div className="text-[11px] text-error mt-1">已存在同名 server</div>}
          </Field>

          <Field label="传输类型">
            <SegmentedControl
              value={transport}
              onChange={(v) => setTransport(v as McpTransport)}
              options={[
                { val: 'stdio', label: 'stdio' },
                { val: 'streamable_http', label: 'HTTP' },
                { val: 'sse', label: 'SSE' },
              ]}
            />
          </Field>

          {stdio ? (
            <>
              <Field label="启动命令">
                <TextInput
                  className="font-mono"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="npx"
                />
              </Field>
              <Field label="参数" hint="回车追加一项；每项对应 args 数组的一个元素">
                <ArgsEditor values={args} onChange={setArgs} />
              </Field>
              <Field label="环境变量" hint="值支持 ${ENV_NAME} 引用外部环境变量">
                <KvEditor rows={env} onChange={setEnv} keyPlaceholder="KEY" />
              </Field>
            </>
          ) : (
            <>
              <Field label="服务器 URL">
                <TextInput
                  className="font-mono"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://example.com/mcp"
                />
              </Field>
              <Field label="请求头 Headers" hint="鉴权在此写，如 Authorization: Bearer ${TOKEN}">
                <KvEditor rows={headers} onChange={setHeaders} keyPlaceholder="Header" />
              </Field>
            </>
          )}

          {/* 高级选项 */}
          <div className="border-t border-line/40 pt-3">
            <button
              type="button"
              onClick={() => setAdvOpen((o) => !o)}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-ink"
            >
              <ChevronDown
                size={13}
                className={`transition-transform ${advOpen ? '' : '-rotate-90'}`}
              />
              高级选项
            </button>
            {advOpen && (
              <div className="mt-3">
                {stdio ? (
                  <Field label="工作目录 cwd">
                    <TextInput
                      className="font-mono"
                      value={cwd}
                      onChange={(e) => setCwd(e.target.value)}
                      placeholder="留空 = 继承 serve 进程目录"
                    />
                  </Field>
                ) : (
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="timeout（秒）">
                      <TextInput
                        type="number"
                        value={timeout}
                        onChange={(e) => setTimeout(e.target.value)}
                        placeholder="5"
                      />
                    </Field>
                    <Field label="sse_read_timeout（秒）">
                      <TextInput
                        type="number"
                        value={sseRead}
                        onChange={(e) => setSseRead(e.target.value)}
                        placeholder="300"
                      />
                    </Field>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <Field label="名称" hint="服务器唯一标识，作为 mcp_server.json 的键名">
            <TextInput
              value={srvName}
              onChange={(e) => setSrvName(e.target.value)}
              placeholder="my-server"
            />
            {dup && <div className="text-[11px] text-error mt-1">已存在同名 server</div>}
          </Field>
          <Field
            label="服务器配置（JSON）"
            hint="直接编辑该 server 的 JSON 片段；切回「表单」时解析回填"
          >
            <textarea
              spellCheck={false}
              value={json}
              onChange={(e) => setJson(e.target.value)}
              className="w-full min-h-[220px] px-3 py-2 rounded-lg text-[12px] font-mono leading-relaxed bg-canvas/50 text-ink border border-line/50 outline-none transition focus:border-primary/50 resize-y"
            />
            {jsonErr && <div className="text-[11px] text-error mt-1.5">{jsonErr}</div>}
          </Field>
        </div>
      )}
    </FormModal>
  )
}

// 参数编辑：有序 chip，允许重复
function ArgsEditor({ values, onChange }: { values: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const v = draft.trim()
    if (v) onChange([...values, v])
    setDraft('')
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {values.map((v, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1.5 bg-surface border border-line rounded-full px-2.5 py-1 text-xs font-mono"
        >
          {v}
          <button
            onClick={() => onChange(values.filter((_, j) => j !== i))}
            className="text-muted-foreground hover:text-ink"
          >
            <X size={11} />
          </button>
        </span>
      ))}
      <span className="inline-flex items-center gap-1 bg-surface border border-dashed border-line rounded-full px-2 py-1">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), add())}
          placeholder="新参数…"
          className="bg-transparent outline-none text-xs font-mono w-28 text-ink"
        />
        <button onClick={add} className="text-muted-foreground hover:text-ink">
          <Plus size={12} />
        </button>
      </span>
    </div>
  )
}

// key-value 行编辑（env / headers）
function KvEditor({
  rows,
  onChange,
  keyPlaceholder,
}: {
  rows: Kv[]
  onChange: (rows: Kv[]) => void
  keyPlaceholder: string
}) {
  const set = (i: number, patch: Partial<Kv>) =>
    onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  return (
    <div className="space-y-1.5">
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[1fr_1.3fr_auto] gap-1.5 items-center">
          <TextInput
            className="h-8 font-mono text-xs"
            value={r.k}
            onChange={(e) => set(i, { k: e.target.value })}
            placeholder={keyPlaceholder}
          />
          <TextInput
            className="h-8 font-mono text-xs"
            value={r.v}
            onChange={(e) => set(i, { v: e.target.value })}
            placeholder="value"
          />
          <button
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
            className="grid place-items-center h-8 w-8 rounded-md text-muted-foreground hover:text-error"
          >
            <X size={14} />
          </button>
        </div>
      ))}
      <button
        onClick={() => onChange([...rows, { k: '', v: '' }])}
        className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-md border border-dashed border-line text-[11.5px] text-muted-foreground hover:text-ink hover:border-separator"
      >
        <Plus size={13} />
        添加一项
      </button>
    </div>
  )
}

// 项目选择器（项目作用范围）：从该机器已登记项目里选一个
function ProjectSelect({
  gw,
  value,
  onChange,
}: {
  gw?: Gateway
  value: string
  onChange: (v: string) => void
}) {
  const [projects, setProjects] = useState<Project[]>([])
  const [creating, setCreating] = useState(false)

  const load = useCallback(() => {
    gw?.listProjects()
      .then((r) => setProjects(r.projects ?? []))
      .catch(() => setProjects([]))
  }, [gw])
  useEffect(() => {
    load()
  }, [load])

  const current = useMemo(() => projects.find((p) => p.path === value), [projects, value])
  const label = current ? current.name : value ? basename(value) : '选择项目…'

  const onCreated = (path: string) => {
    setCreating(false)
    gw?.addProject(path)
      .then((r) => {
        setProjects(r.projects ?? [])
        onChange(path)
      })
      .catch(() => {})
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="group flex shrink-0 items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-left text-xs outline-none transition data-[state=open]:border-primary"
          >
            <Folder size={14} className="shrink-0 text-primary" />
            <span className="truncate max-w-[140px] text-ink">{label}</span>
            <ChevronDown
              size={13}
              className="shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
            />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {projects.map((p) => (
            <DropdownMenuItem key={p.path} onClick={() => onChange(p.path)}>
              <Check className={`text-primary ${p.path === value ? 'opacity-100' : 'opacity-0'}`} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-ink">{p.name}</div>
                <div className="truncate font-mono text-[10px] text-muted-foreground">{p.path}</div>
              </div>
            </DropdownMenuItem>
          ))}
          {projects.length > 0 && <DropdownMenuSeparator />}
          <DropdownMenuItem onClick={() => setCreating(true)} className="text-muted-foreground">
            <FolderPlus />
            新建项目
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {creating && (
        <DirBrowser
          gw={gw}
          title="新建项目"
          onPick={onCreated}
          onCancel={() => setCreating(false)}
        />
      )}
    </>
  )
}

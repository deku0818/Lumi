import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Check,
  ChevronDown,
  Folder,
  FolderPlus,
  Globe,
  Pencil,
  Plus,
  Terminal,
  Trash2,
  X,
} from 'lucide-react'
import type { McpScope, McpServerConfig, McpServers, McpTransport, Project } from '../types'
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

// MCP 管理面板（设置 → MCP）。两个维度：机器（MachineTabs）· 作用范围（全局/项目）。
// 列表：server 卡片（传输 tag + 命令/URL 摘要 + 启用开关 + 编辑/删除）。
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
  const [editing, setEditing] = useState<string | null | undefined>(undefined) // undefined=列表；null=新增；string=编辑该 server

  const gw = gwFor(machine)
  const inProject = scope === 'project'
  // 项目范围但未选项目时不请求（无目标文件）
  const ready = !inProject || !!project

  const reload = useCallback(() => {
    if (inProject && !project) {
      setServers({})
      return
    }
    gwFor(machine)
      ?.listMcpServers(scope, inProject ? project : '')
      .then((r) => setServers(r.servers ?? {}))
      .catch(() => setServers({}))
  }, [gwFor, machine, scope, project, inProject])

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
                onToggle={(on) => toggle(name, on)}
                onEdit={() => setEditing(name)}
                onDelete={() => remove(name)}
              />
            ))}
          </div>
        )}
      </Section>

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
  onToggle,
  onEdit,
  onDelete,
}: {
  name: string
  config: McpServerConfig
  onToggle: (on: boolean) => void
  onEdit: () => void
  onDelete: () => void
}) {
  const t = transportOf(config)
  const off = config.disabled === true
  return (
    <Card className={`flex items-center gap-3 ${off ? 'opacity-55' : ''}`}>
      <div className="grid place-items-center w-9 h-9 rounded-lg bg-surface border border-line text-ink shrink-0">
        {isStdio(t) ? <Terminal size={17} /> : <Globe size={17} />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-medium flex items-center gap-2">
          {name}
          <span className="text-[10px] font-medium uppercase tracking-wide px-1.5 py-px rounded-full border border-separator text-muted-foreground">
            {tagLabel(t)}
          </span>
        </div>
        <div className="text-[11px] mt-0.5 truncate font-mono text-muted-foreground">
          {subOf(config)}
        </div>
      </div>
      <Switch checked={!off} onCheckedChange={onToggle} />
      <Button variant="ghost" size="icon" onClick={onEdit} className="text-muted-foreground h-8 w-8">
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
              <ChevronDown size={13} className={`transition-transform ${advOpen ? '' : '-rotate-90'}`} />
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
          <Field label="服务器配置（JSON）" hint="直接编辑该 server 的 JSON 片段；切回「表单」时解析回填">
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
    gw
      ?.listProjects()
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
    gw
      ?.addProject(path)
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
              <Check
                className={`text-primary ${p.path === value ? 'opacity-100' : 'opacity-0'}`}
              />
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

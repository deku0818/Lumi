import { memo, useCallback, useEffect, useState } from 'react'
import {
  Bot,
  Brain,
  Clock,
  FileText,
  Lock,
  MessageSquare,
  Pencil,
  Pin,
  Plus,
  Send,
  Star,
  Trash2,
  Zap,
} from 'lucide-react'
import type { Gateway } from '../gateway'
import type {
  CronJob,
  ProjectOverview,
  ProjectResource,
  ProjectResourceKind,
  SessionMeta,
} from '../types'
import { useI18n } from '../i18n'
import { Markdown } from './Markdown'
import { toast } from './Toast'
import { errorMessage, timeAgo } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'

// 项目主页（交互定稿见 .demos/project-home.html）：点进项目卡片后的落地页。
// 左列 = 输入岛（发送即在此项目新建会话）+ 该项目会话流；右列 = 项目画像五卡
// （提示词 / 记忆 / 定时任务 / 技能 / 子 Agent），数据一次拉全（project_overview），
// 详情浮层按需读单资源。内置资源只读 + 「复制到项目」；项目层支持增删改。
type Sheet =
  | { mode: 'view'; kind: ProjectResourceKind; name: string }
  | { mode: 'create'; kind: 'skill' | 'agent' }

// memo：App 是流式事件的重渲染热点，props 已在 App 侧全部稳定引用，
// 停在项目主页时后台 token 不再引发整页 reconcile
export const ProjectHomePage = memo(function ProjectHomePage({
  project,
  isDefault,
  api,
  sessions,
  cronJobs,
  onBack,
  onStartChat,
  onOpenSession,
  onOpenScheduled,
  onToggleCron,
}: {
  project: { name: string; path: string }
  isDefault: boolean
  api: () => Gateway | undefined
  sessions: SessionMeta[]
  cronJobs: CronJob[]
  onBack: () => void
  onStartChat: (text: string) => void
  onOpenSession: (tid: string) => void
  onOpenScheduled: () => void
  onToggleCron: (jobId: string, enabled: boolean) => void
}) {
  const { t } = useI18n()
  const [overview, setOverview] = useState<ProjectOverview | null>(null)
  const [draft, setDraft] = useState('')
  const [sheet, setSheet] = useState<Sheet | null>(null)
  const [promptTab, setPromptTab] = useState<'SOUL' | 'AGENTS'>('SOUL')

  // 概览加载：ovTick 驱动重拉（写操作后 +1）；stale 判废防切换项目时旧响应倒灌
  const [ovTick, setOvTick] = useState(0)
  const refresh = useCallback(() => setOvTick((n) => n + 1), [])
  useEffect(() => {
    let stale = false
    api()
      ?.projectOverview(project.path)
      .then((ov) => {
        if (!stale) setOverview(ov)
      })
      .catch(() => {})
    return () => {
      stale = true
    }
  }, [api, project.path, ovTick])

  const submit = () => {
    if (draft.trim()) onStartChat(draft.trim())
  }

  const prompt = overview?.prompts.find((p) => p.name === promptTab)
  const pinned = sessions.filter((s) => s.pinned)
  const recent = sessions.filter((s) => !s.pinned)

  return (
    <div className="flex-1 overflow-auto">
      <div className="max-w-4xl mx-auto w-full px-8 pt-4 pb-12">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-4 select-none">
          <button onClick={onBack} className="hover:text-ink transition">
            {t('projects.title')}
          </button>
          <span>/</span>
          <span className="text-ink">{project.name}</span>
        </div>

        <div className="grid grid-cols-[1fr_280px] gap-7">
          {/* ══ 左列 ══ */}
          <div className="min-w-0">
            <div className="mb-5">
              <div className="flex items-center gap-2">
                <h1 className="serif text-2xl select-none">{project.name}</h1>
                {isDefault && <Star size={14} className="proj-star" fill="currentColor" />}
              </div>
              <div className="mt-1 text-[11.5px] text-muted-foreground font-mono truncate" title={project.path}>
                {project.path}
              </div>
            </div>

            {/* 输入岛：发送即在此项目新建会话并携带首条消息 */}
            <div className="composer-glass rounded-2xl px-4 pt-3 pb-2.5">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault()
                    submit()
                  }
                }}
                placeholder={t('projhome.composerPlaceholder', { name: project.name })}
                rows={2}
                className="w-full bg-transparent outline-none resize-none text-[13.5px] selectable"
              />
              <div className="flex justify-end">
                <button
                  onClick={submit}
                  disabled={!draft.trim()}
                  className="size-7 grid place-items-center rounded-full bg-primary text-primary-foreground disabled:opacity-40 transition"
                >
                  <Send size={13} />
                </button>
              </div>
            </div>

            {pinned.length > 0 && (
              <>
                <SectionLabel>{t('sidebar.pinned')}</SectionLabel>
                {pinned.map((s) => (
                  <SessionRow key={s.thread_id} s={s} onOpen={onOpenSession} />
                ))}
              </>
            )}
            <SectionLabel count={recent.length}>{t('projhome.sessions')}</SectionLabel>
            {recent.length === 0 ? (
              <div className="py-6 text-xs text-muted-foreground select-none">
                {t('projhome.noSessions')}
              </div>
            ) : (
              recent.map((s) => <SessionRow key={s.thread_id} s={s} onOpen={onOpenSession} />)
            )}
          </div>

          {/* ══ 右列 ══ */}
          <div className="flex flex-col gap-3.5 min-w-0">
            {/* 提示词 */}
            <Card
              icon={<FileText size={13} />}
              title={t('projhome.prompts')}
              action={
                <CardAction title={t('projhome.edit')} onClick={() => setSheet({ mode: 'view', kind: 'prompt', name: promptTab })}>
                  <Pencil size={12} />
                </CardAction>
              }
            >
              <div className="flex gap-1 mb-2">
                {(['SOUL', 'AGENTS'] as const).map((n) => {
                  const info = overview?.prompts.find((p) => p.name === n)
                  return (
                    <button
                      key={n}
                      onClick={() => setPromptTab(n)}
                      className={`px-2 py-0.5 rounded-md text-[11px] font-mono transition ${
                        promptTab === n
                          ? 'bg-primary/15 text-primary'
                          : 'text-muted-foreground hover:bg-surface'
                      } ${info?.source ? '' : 'italic opacity-60'}`}
                    >
                      {n}.md
                    </button>
                  )
                })}
              </div>
              {prompt?.source ? (
                <>
                  <div
                    onClick={() => setSheet({ mode: 'view', kind: 'prompt', name: promptTab })}
                    className="text-[11.5px] leading-relaxed text-ink/85 line-clamp-5 cursor-pointer hover:text-ink whitespace-pre-wrap"
                  >
                    {prompt.body.trim()}
                  </div>
                  <div className="mt-2 text-[10.5px] text-muted-foreground/70 font-mono truncate">{prompt.path}</div>
                </>
              ) : (
                <button
                  onClick={() => setSheet({ mode: 'view', kind: 'prompt', name: promptTab })}
                  className="text-left text-[11.5px] text-muted-foreground italic hover:text-ink transition"
                >
                  {t('projhome.promptEmpty')}
                </button>
              )}
            </Card>

            {/* 记忆 */}
            <Card icon={<Brain size={13} />} title={t('projhome.memory')} count={overview?.memory.length}>
              {!overview?.memory.length ? (
                <Empty>{t('projhome.memoryEmpty')}</Empty>
              ) : (
                overview.memory.map((m) => (
                  <button
                    key={m.name}
                    onClick={() => setSheet({ mode: 'view', kind: 'memory', name: m.name })}
                    className={`flex items-center gap-2 w-full text-left px-2 py-1 -mx-2 rounded-lg hover:bg-surface transition ${
                      m.name === 'MEMORY.md' ? 'text-primary' : 'text-ink/85'
                    }`}
                  >
                    <FileText size={11} className="shrink-0 text-muted-foreground/70" />
                    <span className="truncate font-mono text-[11.5px]">{m.name}</span>
                  </button>
                ))
              )}
            </Card>

            {/* 定时任务：机器级 cron（当前实现按机器隔离，非按项目） */}
            <Card
              icon={<Clock size={13} />}
              title={t('projhome.scheduled')}
              count={cronJobs.length}
              action={
                <CardAction title={t('cron.new')} onClick={onOpenScheduled}>
                  <Plus size={12} />
                </CardAction>
              }
            >
              {cronJobs.length === 0 ? (
                <Empty>{t('projhome.scheduledEmpty')}</Empty>
              ) : (
                cronJobs.map((j) => (
                  <div key={j.id} className="flex items-center gap-2 py-1.5 border-t border-line/40 first:border-0">
                    <button onClick={onOpenScheduled} className="flex-1 min-w-0 text-left">
                      <div className="text-xs truncate">{j.name}</div>
                      <div className="text-[10.5px] text-muted-foreground font-mono truncate">
                        {j.schedule.value}
                      </div>
                    </button>
                    <Switch checked={j.enabled} onCheckedChange={(v) => onToggleCron(j.id, v)} />
                  </div>
                ))
              )}
            </Card>

            {/* 技能 */}
            <Card
              icon={<Zap size={13} />}
              title={t('projhome.skills')}
              count={overview?.skills.length}
              action={
                <CardAction title={t('projhome.newSkill')} onClick={() => setSheet({ mode: 'create', kind: 'skill' })}>
                  <Plus size={12} />
                </CardAction>
              }
            >
              {!overview?.skills.length ? (
                <Empty>{t('projhome.skillsEmpty')}</Empty>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {overview.skills.map((s) => (
                    <button
                      key={s.name}
                      title={s.description}
                      onClick={() => setSheet({ mode: 'view', kind: 'skill', name: s.name })}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-surface border border-line/70 text-[11px] font-mono text-ink/85 hover:border-primary/40 transition"
                    >
                      {s.builtin && <Lock size={9} className="text-muted-foreground/70" />}
                      {s.name}
                    </button>
                  ))}
                </div>
              )}
            </Card>

            {/* 子 Agent */}
            <Card
              icon={<Bot size={13} />}
              title={t('projhome.agents')}
              count={overview?.agents.length}
              action={
                <CardAction title={t('projhome.newAgent')} onClick={() => setSheet({ mode: 'create', kind: 'agent' })}>
                  <Plus size={12} />
                </CardAction>
              }
            >
              {!overview?.agents.length ? (
                <Empty>{t('projhome.agentsEmpty')}</Empty>
              ) : (
                overview.agents.map((a) => (
                  <button
                    key={a.name}
                    onClick={() => setSheet({ mode: 'view', kind: 'agent', name: a.name })}
                    className="flex items-start gap-2 w-full text-left py-1.5 border-t border-line/40 first:border-0"
                  >
                    {a.builtin ? (
                      <Lock size={11} className="shrink-0 mt-0.5 text-muted-foreground/70" />
                    ) : (
                      <Bot size={11} className="shrink-0 mt-0.5 text-muted-foreground/70" />
                    )}
                    <span className="min-w-0">
                      <span className="block text-xs font-mono">{a.name}</span>
                      <span className="block text-[10.5px] text-muted-foreground truncate">{a.description}</span>
                    </span>
                  </button>
                ))
              )}
            </Card>
          </div>
        </div>
      </div>

      {sheet && (
        <ResourceSheet
          // 按资源身份 remount：换资源打开时重置 file/editing/text 等内部状态
          key={sheet.mode === 'view' ? `${sheet.kind}:${sheet.name}` : `new:${sheet.kind}`}
          api={api}
          path={project.path}
          sheet={sheet}
          onClose={() => setSheet(null)}
          onChanged={refresh}
          onSwitch={(s) => setSheet(s)}
        />
      )}
    </div>
  )
})

function SectionLabel({ children, count }: { children: React.ReactNode; count?: number }) {
  return (
    <div className="flex items-baseline gap-2 mt-6 mb-1.5 text-xs text-muted-foreground font-medium select-none">
      {children}
      {count !== undefined && count > 0 && <span className="text-[10.5px] opacity-70">{count}</span>}
    </div>
  )
}

function SessionRow({ s, onOpen }: { s: SessionMeta; onOpen: (tid: string) => void }) {
  const { lang } = useI18n()
  return (
    <button
      onClick={() => onOpen(s.thread_id)}
      className="flex items-center gap-2.5 w-full text-left px-3 py-2 -mx-3 rounded-xl hover:bg-surface/75 transition"
    >
      {s.pinned ? (
        <Pin size={13} className="shrink-0 text-primary" />
      ) : (
        <MessageSquare size={13} className="shrink-0 text-muted-foreground" />
      )}
      <span className="flex-1 min-w-0">
        <span className="block text-[13px] truncate">{s.title || s.first_message}</span>
        {s.title && s.first_message && (
          <span className="block text-[11px] text-muted-foreground truncate">{s.first_message}</span>
        )}
      </span>
      {/* 现算相对时间：本地化跟随界面语言，且不会像后端快照那样越停越不准 */}
      <span className="shrink-0 text-[10.5px] text-muted-foreground/70">
        {timeAgo(Date.parse(s.created_at) / 1000, lang)}
      </span>
    </button>
  )
}

function Card({
  icon,
  title,
  count,
  action,
  children,
}: {
  icon: React.ReactNode
  title: string
  count?: number
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="rounded-2xl border border-line/45 bg-panel/70 px-3.5 py-3">
      <div className="flex items-center gap-1.5 mb-2 select-none">
        <span className="text-muted-foreground">{icon}</span>
        <span className="text-xs font-semibold">{title}</span>
        {count !== undefined && count > 0 && (
          <span className="text-[10.5px] text-muted-foreground/70">{count}</span>
        )}
        <span className="ml-auto">{action}</span>
      </div>
      {children}
    </div>
  )
}

function CardAction({
  title,
  onClick,
  children,
}: {
  title: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className="size-6 grid place-items-center rounded-md text-muted-foreground hover:bg-surface hover:text-ink transition"
    >
      {children}
    </button>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="text-[11.5px] text-muted-foreground/80 italic select-none">{children}</div>
}

// ── 详情浮层：查看 / 编辑 / 删除 / 复制内置 / 新建，四类资源共用 ──

const SKILL_TEMPLATE = `---
name: {name}
description: 一句话说明这个技能做什么、什么时候用
---

# 流程

1. `

const AGENT_TEMPLATE = `---
name: {name}
description: 一句话说明这个 Agent 负责什么
# tools: read, grep   # 可选：限制工具白名单
---

（正文即系统提示词）
`

// 与后端 _NAME_RE 一致：首字符字母数字，其余允许 ._-（不一致会被后端拒绝）
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

function ResourceSheet({
  api,
  path,
  sheet,
  onClose,
  onChanged,
  onSwitch,
}: {
  api: () => Gateway | undefined
  path: string
  sheet: Sheet
  onClose: () => void
  onChanged: () => void
  onSwitch: (s: Sheet) => void
}) {
  const { t } = useI18n()
  const creating = sheet.mode === 'create'
  const kind = sheet.kind
  const [res, setRes] = useState<ProjectResource | null>(null)
  const [file, setFile] = useState('')
  const [editing, setEditing] = useState(creating)
  const [text, setText] = useState('')
  const [newName, setNewName] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [tick, setTick] = useState(0) // 写操作后 +1 触发重读（body 由后端剥，不本地拼）
  // create 模式：用户动过正文前，模板的 name 行跟随名字输入框实例化——
  // 前端不做 frontmatter 手术（解析权威在后端），动过就原样提交、交给后端校验报错
  const [textDirty, setTextDirty] = useState(false)

  useEffect(() => {
    if (sheet.mode === 'create') {
      if (!textDirty) {
        setText(
          (sheet.kind === 'skill' ? SKILL_TEMPLATE : AGENT_TEMPLATE).replace(
            '{name}',
            newName.trim(),
          ),
        )
      }
      return
    }
    let stale = false
    api()
      ?.projectResourceRead(path, sheet.kind, sheet.name, file)
      .then((r) => {
        if (stale) return
        setRes(r)
        setText(r.content)
        // 未配置的提示词（三层都没有）：没有可看的，直接落到编辑态开写
        if (sheet.kind === 'prompt' && !r.content) setEditing(true)
      })
      .catch((e) => {
        if (!stale) toast.error(errorMessage(e))
      })
    return () => {
      stale = true
    }
  }, [api, path, sheet, file, tick, newName, textDirty])

  const builtin = !creating && !!res?.builtin
  // 提示词的编辑恒写项目层（即使当前命中 style/builtin——保存即产生项目覆盖）
  const editable = creating || kind === 'prompt' || (!builtin && kind !== 'memory')
  const validNewName = NAME_RE.test(newName.trim())

  const requireGw = (): Gateway | undefined => {
    const gw = api()
    if (!gw) toast.error(t('projhome.offline'))
    return gw
  }

  const save = async () => {
    const gw = requireGw()
    if (!gw) return
    try {
      if (sheet.mode === 'create') {
        const target = newName.trim() // 合法性由创建按钮的 disabled 把关
        await gw.projectResourceWrite(path, kind, target, text)
        onChanged()
        onSwitch({ mode: 'view', kind: sheet.kind, name: target })
      } else {
        await gw.projectResourceWrite(path, kind, sheet.name, text, kind === 'skill' ? file : '')
        onChanged()
        onClose()
      }
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  const doDelete = async () => {
    if (sheet.mode !== 'view') return
    const gw = requireGw()
    if (!gw) return
    try {
      await gw.projectResourceDelete(path, kind as 'skill' | 'agent', sheet.name)
      onChanged()
      onClose()
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  const copyToProject = async () => {
    if (sheet.mode !== 'view') return
    const gw = requireGw()
    if (!gw) return
    try {
      await gw.projectCopyBuiltin(path, kind as 'skill' | 'agent', sheet.name)
      onChanged()
      setFile('')
      setTick((n) => n + 1) // 重读项目层副本
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  const kindLabel = t(kind === 'skill' ? 'projhome.skillKind' : 'projhome.agentKind')

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl w-full p-0 gap-0 overflow-hidden" showCloseButton>
        {/* 头部 */}
        <div className="flex items-center gap-2.5 px-5 py-3.5 border-b border-line/60 pr-12">
          {creating ? (
            <input
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={t('projhome.namePlaceholder')}
              className={`bg-canvas/60 border rounded-lg px-2.5 py-1 text-[13px] font-mono outline-none w-64 ${
                newName.trim() && !validNewName
                  ? 'border-destructive/60'
                  : 'border-line focus:border-primary/40'
              }`}
            />
          ) : (
            <DialogTitle className="text-[13.5px] font-mono font-semibold">{sheet.name}</DialogTitle>
          )}
          {builtin && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-muted-foreground/12 text-[10px] text-muted-foreground">
              <Lock size={9} />
              {t(res?.source === 'global' ? 'projhome.globalBadge' : 'projhome.builtin')}
            </span>
          )}
          {res?.path && !creating && (
            <span className="text-[10.5px] text-muted-foreground/70 font-mono truncate">{res.path}</span>
          )}
          {creating && (
            <span className="text-[10.5px] text-muted-foreground/70 font-mono">
              {kind === 'skill' ? '.lumi/skills/…/SKILL.md' : '.lumi/agents/….md'}
            </span>
          )}
          <span className="ml-auto flex items-center gap-0.5">
            {editable && !editing && (
              <SheetBtn title={t('projhome.edit')} onClick={() => setEditing(true)}>
                <Pencil size={13} />
              </SheetBtn>
            )}
            {!creating && !builtin && (kind === 'skill' || kind === 'agent') && (
              <SheetBtn danger title={t('projhome.delete')} onClick={() => setConfirming(true)}>
                <Trash2 size={13} />
              </SheetBtn>
            )}
          </span>
        </div>

        {/* 删除确认条 */}
        {confirming && (
          <div className="flex items-center gap-2.5 px-5 py-2.5 text-xs bg-destructive/10 border-b border-destructive/30">
            <span className="flex-1">
              {t('projhome.deleteConfirm', { name: sheet.mode === 'view' ? sheet.name : '' })}
            </span>
            <Button variant="outline" size="sm" onClick={() => setConfirming(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="destructive" size="sm" onClick={doDelete}>
              {t('projhome.delete')}
            </Button>
          </div>
        )}

        {/* 内置提示条 */}
        {builtin && !confirming && (kind === 'skill' || kind === 'agent') && (
          <div className="flex items-center gap-2.5 px-5 py-2.5 text-[11.5px] text-muted-foreground border-b border-line/60">
            {t(res?.source === 'global' ? 'projhome.globalTip' : 'projhome.builtinTip', {
              kind: kindLabel,
            })}
            <button
              onClick={copyToProject}
              className="px-2.5 py-1 rounded-full border border-dashed border-primary/45 text-primary/90 hover:bg-primary/10 transition"
            >
              {t('projhome.copyToProject')}
            </button>
            <span className="text-muted-foreground/60">{t('projhome.copyNote')}</span>
          </div>
        )}

        {/* 正文：skill 多文件时左侧文件栏。min-w-0 阻断 grid track 被长内容撑宽
            （DialogContent 是 grid，track 变宽会把头部按钮推出 overflow-hidden 裁剪区） */}
        <div className="flex min-h-0 min-w-0 h-[26rem]">
          {kind === 'skill' && (res?.files?.length ?? 0) > 1 && !creating && (
            <div className="w-44 shrink-0 overflow-y-auto border-r border-line/60 p-2">
              {res!.files!.map((f) => (
                <button
                  key={f}
                  onClick={() => {
                    setFile(f === 'SKILL.md' ? '' : f)
                    setEditing(false)
                  }}
                  className={`block w-full text-left px-2 py-1 rounded-md text-[11px] font-mono truncate transition ${
                    (file || 'SKILL.md') === f
                      ? 'bg-primary/12 text-primary'
                      : 'text-muted-foreground hover:bg-surface hover:text-ink'
                  }`}
                >
                  {f}
                </button>
              ))}
            </div>
          )}
          <div className="flex-1 min-w-0 flex flex-col">
            {editing ? (
              <>
                <textarea
                  value={text}
                  onChange={(e) => {
                    setText(e.target.value)
                    if (creating) setTextDirty(true) // 动过正文后模板不再跟随名字
                  }}
                  spellCheck={false}
                  className="flex-1 resize-none outline-none bg-canvas/50 px-4 py-3 font-mono text-[11.5px] leading-relaxed selectable"
                />
                <div className="flex justify-end gap-2 px-4 py-2.5 border-t border-line/60">
                  <Button variant="outline" size="sm" onClick={() => (creating ? onClose() : setEditing(false))}>
                    {t('common.cancel')}
                  </Button>
                  <Button size="sm" disabled={creating && !validNewName} onClick={save}>
                    {t(creating ? 'projhome.create' : 'projhome.save')}
                  </Button>
                </div>
              </>
            ) : (
              <div className="flex-1 overflow-y-auto px-5 py-4 md text-[12.5px] selectable">
                <Markdown>{res?.body ?? ''}</Markdown>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function SheetBtn({
  title,
  danger,
  onClick,
  children,
}: {
  title: string
  danger?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className={`size-7 grid place-items-center rounded-md text-muted-foreground transition ${
        danger ? 'hover:bg-destructive/15 hover:text-destructive' : 'hover:bg-surface hover:text-ink'
      }`}
    >
      {children}
    </button>
  )
}

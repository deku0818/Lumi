import { useMemo, useState } from 'react'
import {
  Check,
  ChevronDown,
  Folder,
  MoreVertical,
  Pencil,
  Plus,
  Search,
  Trash2,
} from 'lucide-react'
import type { Project } from '../types'
import { useI18n } from '../i18n'
import { RenameInput } from './Sidebar'
import { timeAgo } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu'

type SortKey = 'recent' | 'name'

// 项目管理页（交互定稿见 .demos/lumi-projects.html）：搜索 + 排序 + 卡片网格。
// 当前项目卡片金描边 + 名旁静止金点（挂载时光环一闪）；点击卡片切换项目。
export function ProjectsPage({
  projects,
  current,
  onOpen,
  onNew,
  onRemove,
  onRename,
}: {
  projects: Project[]
  current: string
  onOpen: (path: string) => void
  onNew: () => void
  onRemove: (path: string) => void
  onRename: (path: string, name: string) => void
}) {
  const { t } = useI18n()
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<SortKey>('recent')
  const [renaming, setRenaming] = useState<string | null>(null)

  // 后端已按最近使用降序下发，名称排序在前端做
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = projects.filter(
      (p) => !q || p.name.toLowerCase().includes(q) || p.path.toLowerCase().includes(q),
    )
    return sort === 'name' ? [...list].sort((a, b) => a.name.localeCompare(b.name)) : list
  }, [projects, query, sort])

  return (
    <div className="flex-1 overflow-auto">
      <div className="max-w-3xl mx-auto w-full px-8 pt-4 pb-10">
        <div className="flex items-center gap-2 mb-5">
          <h1 className="serif text-2xl flex-1 select-none">{t('projects.title')}</h1>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-surface/70 hover:text-ink transition outline-none">
                {t(sort === 'recent' ? 'projects.sortRecent' : 'projects.sortName')}
                <ChevronDown size={13} />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-40">
              {(['recent', 'name'] as const).map((k) => (
                <DropdownMenuItem key={k} onClick={() => setSort(k)}>
                  <span className="flex-1">
                    {t(k === 'recent' ? 'projects.sortRecent' : 'projects.sortName')}
                  </span>
                  {sort === k && <Check className="text-primary" />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button onClick={onNew} className="rounded-xl gap-1.5">
            <Plus className="size-3.5" />
            {t('projects.new')}
          </Button>
        </div>

        <div className="flex items-center gap-2.5 mb-5 rounded-xl bg-surface border border-line/50 px-3.5 py-2.5 text-muted-foreground focus-within:border-primary/40 transition-colors">
          <Search size={14} className="shrink-0" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('projects.search')}
            className="flex-1 bg-transparent outline-none text-ink text-sm"
          />
        </div>

        {shown.length === 0 ? (
          <div className="py-20 text-center text-sm text-muted-foreground select-none">
            {t('projects.empty')}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {shown.map((p) => (
              <ProjectCard
                key={p.path}
                project={p}
                active={p.path === current}
                renaming={renaming === p.path}
                onOpen={onOpen}
                onRemove={onRemove}
                onRenameStart={() => setRenaming(p.path)}
                onRenameDone={(name) => {
                  setRenaming(null)
                  if (name !== null) onRename(p.path, name)
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function ProjectCard({
  project,
  active,
  renaming,
  onOpen,
  onRemove,
  onRenameStart,
  onRenameDone,
}: {
  project: Project
  active: boolean
  renaming: boolean
  onOpen: (path: string) => void
  onRemove: (path: string) => void
  onRenameStart: () => void
  onRenameDone: (name: string | null) => void
}) {
  const { t, lang } = useI18n()
  return (
    <div className="group relative">
      <button
        onClick={() => onOpen(project.path)}
        className={`w-full text-left rounded-2xl border p-4 transition ${
          active
            ? 'border-primary/40 bg-primary/5'
            : 'bg-surface border-line/45 hover:border-separator'
        }`}
      >
        <div className="flex items-center gap-2 pr-6">
          <Folder size={15} className="shrink-0 text-muted-foreground" />
          {renaming ? (
            <RenameInput initial={project.name} onResolve={onRenameDone} />
          ) : (
            <span className="truncate text-[13.5px] font-semibold">{project.name}</span>
          )}
          {active && !renaming && <span className="proj-dot" />}
        </div>
        <div
          className="mt-2 truncate text-[11.5px] text-muted-foreground font-mono"
          title={project.path}
        >
          {project.path}
        </div>
        <div className="mt-1.5 text-[11px] text-muted-foreground/75">
          {timeAgo(project.last_used, lang)}
        </div>
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            aria-label={t('sidebar.sessionActions')}
            className="absolute right-2.5 top-2.5 size-6 grid place-items-center rounded-md text-muted-foreground hover:bg-line/30 hover:text-ink transition opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100 outline-none"
          >
            <MoreVertical size={15} />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-44">
          <DropdownMenuItem onClick={onRenameStart}>
            <Pencil />
            {t('projects.rename')}
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={() => onRemove(project.path)}>
            <Trash2 />
            {t('projects.remove')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

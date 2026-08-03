import { memo } from 'react'
import { Check } from 'lucide-react'
import type { TodoItem } from '../types'
import { useI18n } from '../i18n'
import { RailSection } from './RightRail'

// 任务进度模块（挂在统一右栏 RightRail 里）：todos 工具的全量快照平铺展示。
// 实时更新经 todos.update 事件、历史还原经 load_history 的 state.todos（同一真相源）。
// in_progress = lumi-orb 呼吸光点（一静一动：光点动、文字静）；completed = 金色 ✓ +
// 文字淡化；全部完成整节灰化保留，等下一轮 todos 覆盖；空列表由 App 侧不渲染本节。

const statusMark = (status: TodoItem['status']) => {
  if (status === 'completed') return <Check size={13} className="text-primary todo-check-in" />
  if (status === 'in_progress') return <span className="lumi-orb scale-[0.8]" />
  return <span className="size-[7px] rounded-full border-[1.5px] border-accent-dim" />
}

export const TodosSection = memo(function TodosSection({ todos }: { todos: TodoItem[] }) {
  const { t } = useI18n()
  const done = todos.filter((x) => x.status === 'completed').length
  const allDone = done === todos.length
  return (
    <RailSection
      title={t('todos.title')}
      count={allDone ? `✓ ${t('todos.allDone')}` : `${done}/${todos.length}`}
    >
      <div className={`transition-opacity duration-500 ${allDone ? 'opacity-60' : ''}`}>
        {todos.map((td, i) => (
          <div key={i} className="flex items-center gap-2 px-1 py-1 min-h-7">
            <span className="w-4 h-4 grid place-items-center shrink-0">
              {statusMark(td.status)}
            </span>
            <span
              className={`selectable text-xs leading-snug line-clamp-2 ${
                td.status === 'in_progress' ? 'text-ink' : 'text-muted-foreground'
              } ${td.status === 'completed' ? 'opacity-60' : ''}`}
            >
              {td.content}
            </span>
          </div>
        ))}
      </div>
    </RailSection>
  )
})

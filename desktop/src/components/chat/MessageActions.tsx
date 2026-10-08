import { memo, useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, Copy, Info, type LucideIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useI18n } from '../../i18n'

export const USER_BUBBLE = 'bg-surface rounded-3xl rounded-br-lg px-4 py-2.5 whitespace-pre-wrap'

// 用户气泡的原地编辑态：气泡原位换成可编辑框 + Cancel/Save（对齐 ChatGPT 编辑交互）。
// Save 前零副作用——截断与重发全部发生在 onSave 回调里。Enter 保存、Esc 取消。
export function EditBubble({
  initial,
  onCancel,
  onSave,
}: {
  initial: string
  onCancel: () => void
  onSave: (text: string) => void
}) {
  const { t } = useI18n()
  const [text, setText] = useState(initial)
  const ref = useRef<HTMLTextAreaElement>(null)
  const submit = () => text.trim() && onSave(text.trim())
  // 进入编辑即聚焦、光标置尾（高度自适应交给 .composer 的 field-sizing）
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.focus()
    el.selectionStart = el.selectionEnd = el.value.length
  }, [])
  return (
    <div className="flex flex-col items-end gap-2">
      <textarea
        ref={ref}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          // 输入法组合中的 Enter 是选字确认，不是提交（与 Composer 同守卫）——
          // 编辑提交是截断历史的破坏性操作，误触代价远高于普通发送
          if (e.nativeEvent.isComposing) return
          if (e.key === 'Escape') onCancel()
          else if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            submit()
          }
        }}
        className={`composer w-full resize-none outline-none ring-1 ring-primary/60 ${USER_BUBBLE}`}
      />
      <div className="flex items-center gap-1.5">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="mr-1 inline-flex cursor-help text-muted-foreground/70">
              <Info size={13} />
            </span>
          </TooltipTrigger>
          <TooltipContent>{t('chat.editHint')}</TooltipContent>
        </Tooltip>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        <Button size="sm" onClick={submit} disabled={!text.trim()}>
          {t('common.save')}
        </Button>
      </div>
    </div>
  )
}

// 消息下方的悬停操作条：鼠标移到该段才淡入。用户气泡右对齐（贴气泡）、助手左对齐。
export function HoverActions({
  node,
  align,
  children,
}: {
  node: ReactNode
  align: 'start' | 'end'
  children: ReactNode
}) {
  return (
    <div className="group/act">
      {node}
      <div
        className={`mt-1 flex opacity-0 group-hover/act:opacity-100 transition-opacity ${
          align === 'end' ? 'justify-end' : '-ml-1'
        }`}
      >
        {children}
      </div>
    </div>
  )
}

// 操作条里的图标按钮（重新生成 / 编辑）：title 与 aria-label 同一文案，样式统一
export function IconAction({
  icon: Icon,
  label,
  onClick,
}: {
  icon: LucideIcon
  label: string
  onClick: () => void
}) {
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={onClick}
      title={label}
      aria-label={label}
      className="text-muted-foreground"
    >
      <Icon />
    </Button>
  )
}

// 消息下的复制按钮：悬停出现，点击复制原文，1.5s 内显示「已复制」反馈。
// memo：聊天流每个 delta 都重渲染，而本按钮的 props 只是一段文本——比较即可整体跳过。
export const CopyButton = memo(function CopyButton({ text }: { text: string }) {
  const { t } = useI18n()
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      })
      .catch(() => {})
  }
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={copy}
      title={copied ? t('common.copied') : t('common.copy')}
      aria-label={t('common.copy')}
      className="text-muted-foreground"
    >
      {copied ? <Check className="text-success" /> : <Copy />}
    </Button>
  )
})

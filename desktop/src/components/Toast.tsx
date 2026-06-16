import { useEffect, useState, useSyncExternalStore } from 'react'
import { AlertCircle, CheckCircle2, Info } from 'lucide-react'

// 可复用的应用内轻量通知通道：任意模块 import { toast } 后调用 toast.error(...) 即可，
// 无需 context / prop 透传。<ToastHost/> 在根部挂一次（见 main.tsx），顶部细条幅展示。

type ToastKind = 'error' | 'success' | 'info'
type ToastEntry = { id: number; kind: ToastKind; message: string; leaving: boolean }

const EXIT_MS = 200 // 退场动画时长，与 CSS transition 对齐

let entries: ToastEntry[] = []
let seq = 0
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((l) => l())

function remove(id: number) {
  entries = entries.filter((e) => e.id !== id)
  emit()
}

function startLeave(id: number) {
  entries = entries.map((e) => (e.id === id ? { ...e, leaving: true } : e))
  emit()
  setTimeout(() => remove(id), EXIT_MS)
}

function push(message: string, kind: ToastKind, duration: number) {
  const id = ++seq
  entries = [...entries, { id, kind, message, leaving: false }]
  emit()
  setTimeout(() => startLeave(id), duration)
}

export const toast = {
  error: (message: string, duration = 3500) => push(message, 'error', duration),
  success: (message: string, duration = 2500) => push(message, 'success', duration),
  info: (message: string, duration = 3000) => push(message, 'info', duration),
}

const subscribe = (cb: () => void) => {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

const ICON = { error: AlertCircle, success: CheckCircle2, info: Info }
// 容器只给边框 + 淡底（文字统一 text-ink）；图标单独上语义色，避免与正文色冲突
const TONE = {
  error: 'border-error/35 bg-error/10',
  success: 'border-success/35 bg-success/10',
  info: 'border-info/35 bg-info/10',
}
const ICON_COLOR = { error: 'text-error', success: 'text-success', info: 'text-info' }

export function ToastHost() {
  const items = useSyncExternalStore(subscribe, () => entries)
  return (
    <div className="fixed top-3 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2 pointer-events-none">
      {items.map((e) => (
        <ToastRow key={e.id} entry={e} />
      ))}
    </div>
  )
}

function ToastRow({ entry }: { entry: ToastEntry }) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const r = requestAnimationFrame(() => setShown(true))
    return () => cancelAnimationFrame(r)
  }, [])
  const Icon = ICON[entry.kind]
  const visible = shown && !entry.leaving
  return (
    <div
      className={`inline-flex items-center gap-2 rounded-full border px-4 py-1.5 text-[12.5px] text-ink bg-panel shadow-lg transition-all duration-200 ${TONE[entry.kind]} ${
        visible ? 'opacity-100 translate-y-0' : 'opacity-0 -translate-y-2'
      }`}
    >
      <Icon size={14} className={`shrink-0 ${ICON_COLOR[entry.kind]}`} />
      <span>{entry.message}</span>
    </div>
  )
}

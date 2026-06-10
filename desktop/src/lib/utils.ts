import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// 文本截断与路径文件名提取（工具标题 / 计划对话框等共用）
export const clip = (s: string, n = 72) => (s.length > n ? s.slice(0, n) + '…' : s)
export const basename = (p: string) => p.split('/').filter(Boolean).pop() || p

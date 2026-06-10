// 斜杠命令解析（对齐后端 lumi/tui/slash_commands/parser.py）。
import type { SlashCommand } from './types'

// 命令模式：以 "/" 开头、命令名尚未输完（无空格、无换行）时展示补全菜单。
export function isCommandMode(text: string): boolean {
  return text.startsWith('/') && !text.slice(1).includes(' ') && !text.includes('\n')
}

// 提取命令前缀（"/" 后到第一个空格之间的子串）。"/rev" -> "rev"
export function commandPrefix(text: string): string {
  return text.slice(1).split(' ', 1)[0]
}

// 解析命令输入，返回 [命令名, 额外文本]。"/review a b" -> ["review", "a b"]
export function parseCommand(text: string): [string, string] {
  const name = commandPrefix(text)
  const rest = text.slice(1 + name.length)
  return [name, rest.replace(/^ +/, '')]
}

// 前缀匹配候选命令（补全菜单数据源）。
export function matchCommands(commands: SlashCommand[], prefix: string): SlashCommand[] {
  return commands.filter((c) => c.name.startsWith(prefix))
}

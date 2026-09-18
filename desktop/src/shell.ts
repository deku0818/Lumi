// shell 命令的轻量着色分词（工具行展示 bash 参数用）：不做完整语法，只分出
// 命令名（行首 / 运算符后 / 换行后首词）、-flag、引号串、$VAR、&& | ; > 运算符、数字，其余原样。
// hl 对应代码块 --hl-* 色板名，随亮暗主题切换。

export type ShellToken = { text: string; hl?: string }

const TOKEN =
  /("(?:[^"\\]|\\.)*"|'[^']*')|(&&|\|\||[|;>])|(\$\{?\w+\}?)|((?:^|(?<=\s))-{1,2}[\w-]*)|(\b\d+\b)|([^\s|;&>"'$]+)|(\s+|.)/g

export function shellTokens(cmd: string): ShellToken[] {
  const out: ShellToken[] = []
  let expectCmd = true
  for (const [text, str, op, variable, flag, num, word] of cmd.matchAll(TOKEN)) {
    // 命令前的 FOO=1 环境赋值不占命令位
    const isCmd = !!word && expectCmd && !word.includes('=')
    const hl = str ? 'string'
      : op ? 'operator'
      : variable ? 'type'
      : flag ? 'keyword'
      : isCmd ? 'function'
      : word && expectCmd ? 'variable'
      : num ? 'number'
      : undefined
    // 引号串里的换行不算换命令
    if (op || (!str && text.includes('\n'))) expectCmd = true
    else if (isCmd) expectCmd = false
    out.push({ text, hl })
  }
  return out
}

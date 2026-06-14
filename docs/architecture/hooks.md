# Hook 机制设计

> 状态：已实施（2026-06-14，v0.1.0a17）。参考实现 `test/OmniAgent`，按 Lumi 极简哲学裁剪。

在 Agent 生命周期的关键事件上注入外部逻辑（合规审计、工具拦截、提醒注入、
结构化输出兜底），无需改框架内核。代码在 `lumi/agents/core/hooks/`。

## 事件与插桩点

| 事件 | 何时触发 | 插桩位置 | dispatch 模式 |
|---|---|---|---|
| **Stop** | 模型不调工具想结束 | `OnAgentStop` 节点（`nodes.py`） | first_intercept |
| **PreToolUse** | 工具执行前 | `tool_executor` 节点开头 | collect |
| **PostToolUse** | 工具结果合并后 | `tool_executor` 节点末尾 | collect |
| UserPromptSubmit / SessionStart / SessionEnd | — | 枚举已定义，**未插桩**（Lumi 拓扑/会话层与 OmniAgent 不同，按需再做） | — |

**为何 Stop 用独立节点**：`is_use_tool` 是纯条件路由函数，只能返回字符串、不能返回
`Command`。而 hook 要能注入消息（`AdditionalContext`）或改路由（`Block`），必须落在
能返回 `Command` 的节点里。故无 tool_calls 时路由到 `OnAgentStop` 薄节点，节点内
`dispatch_hooks` 后默认 `Command(goto=END)`，hook 可拦截。

## 返回值与三模式

Hook 签名 `async (HookContext) -> HookResult`，`HookResult = None | Command | AdditionalContext | Block`：

- `None` — 放行
- `AdditionalContext(text)` — 软扩展：注入 `<system-reminder>` 让模型继续（带 `is_meta`
  + `is_hook_reminder` 标记，TUI 不渲染为用户气泡、轮边界扫描据此跳过，见下）
- `Block(reason)` — 硬终止：拒绝执行 + 以 reason 收尾
- `Command(...)` — 完全控制 graph 路由 + state

`dispatch.py` 的三模式（`_to_command` 把 `AdditionalContext`/`Block` 翻译为 `Command`）：

- **first_intercept**（Stop）：第一个返非 `None` 的 hook 拦截，后续不跑。
- **collect**（PreToolUse/PostToolUse）：多个 `AdditionalContext` 合并到同一条消息；遇
  `Block`/`Command` 短路但已收的 reminder 一起注入。
- **side_effect**（SessionEnd 预留）：所有 hook 并发跑，返回值仅 warning。

错误隔离：每个 hook 包 try/except，单个抛错记日志后继续，dispatch 不抛。

## 三种形态

三形态在 dispatch 层完全等价——非 Python 形态由 wrapper 包装为 Python `Hook` 后
`register_hook` 注册，dispatch 不感知形态差异。

- **Python callable**：进程内直接 await（内置 hook 走这条，见 `builtin.py`）。
- **Shell 命令**（`exec_shell.py`）：subprocess，stdin 喂决策 JSON、stdout 读回。5s 超时
  → SIGTERM→SIGKILL；env 仅透传 `LUMI_HOOK_*` 前缀 + `PATH`（防 secrets 泄露）；
  exit code 0=解析 stdout / 2=deny / 其他=非阻断放行。
- HTTP webhook / Subagent：**未实现**（对桌面 TUI 偏重，按需再加）。

### Shell hook 决策协议（`protocol.py`）

输入（stdin JSON）：`{version, event, thread_id, payload, messages_tail}`。
输出（stdout JSON）：`{decision: "allow"|"deny"|"passthrough", additionalContext?, stopReason?}`
→ `deny` 翻译为 `Block`，`additionalContext` 翻译为 `AdditionalContext`。
`matcher` 正则仅 PreToolUse/PostToolUse 生效，未命中则跳过 subprocess。

## 配置：三级 hooks.json

与 `permissions.json` 同级同模式（JSONC，优先级低→高）：

1. 用户全局 `~/.lumi/hooks.json`
2. 项目共享 `{project}/.lumi/hooks.json`
3. 项目本地 `{project}/.lumi/hooks.local.json`

格式（顶层 event → spec 数组）：

```jsonc
{
  "PreToolUse": [
    { "command": "/abs/path/audit.sh", "matcher": "bash", "timeout": 5000 }
  ],
  "Stop": [{ "command": "/abs/path/on_stop.sh" }]
}
```

每条 spec：`command`（必填，绝对路径可执行）、`matcher`（可选正则）、`timeout`（可选 ms，
默认 5000）。**容错策略**：单条构造失败（路径不存在/不可执行/正则非法）→ log 跳过，
不让整个 agent 起不来（Lumi 面向非技术用户，hook 是高级特性）。

加载由 `create_agent` 调 `load_hooks(Path.cwd())`（幂等，`_LOADED` 守卫）；desktop
切工作目录时 `bridge.set_workspace` 调 `reset_hooks()` + `load_hooks(target)` 重载。
顺序：配置 hook 整体优先于 builtin Python hook，同事件内按声明顺序（逆序 `prepend_hook`）。

## 与结构化输出的联动

结构化输出已从「伪工具拦截」改为「真工具执行」（详见 `structured_tool.py`）：
`__structured_output__` 是**真工具**进 `tool_executor` 执行，闭包内 jsonschema 校验，
失败 return `ToolMessage(status=error)` 让模型修正重试。两处安全阀依赖 hook：

- **Stop hook 兜底**（`builtin.structured_output_stop_hook`）：模型纯文本结束但
  `output_schema` 仍要求结构化输出时，注入 reminder 拉回 CallModel；拉回上限
  `MAX_STOP_PULLBACKS=3`，超过放弃 END，避免 OnAgentStop↔CallModel 死循环。
- **连续失败保护**（`tool_executor` 末尾）：本轮连续校验失败 `>= MAX_CONSECUTIVE_FAILURES=5`
  强制 `goto=END`，防烧 token。

这套联动让去掉硬编码 `tool_choice="any"`（与 Anthropic thinking 的 400 冲突）成为可能——
改由模型自决 + Stop hook 兜底。

### 轮边界判定：reminder 不是边界

连续失败计数 / 拉回计数 / accepted 判定都要界定「本轮窗口」。hook 注入的 reminder 是
**轮内合成插话、不是轮边界**；后台任务通知等真实 meta（`is_meta` 但非 `is_hook_reminder`）
是模型要响应的新输入、**构成轮边界**。共享遍历器 `meta_message.iter_current_turn` 收口
这一判定（跳过 reminder、在真实 HumanMessage 处停），三个扫描器复用，避免各自重复且漏判。

## 内部工具

`is_internal_tool`（`structured_tool.py` 的 `INTERNAL_TOOL_NAMES` 单一事实源）判定框架
内部伪工具（当前仅 `__structured_output__`）。内部工具：(1) 不暴露给用户 hook 的
PreToolUse/PostToolUse payload（宽 matcher 不会误触发）；(2) **纯内部批次**绕过权限审批
快速进 ToolExecutor，但与其他工具**混合的批次不绕过**，照常走权限评估。

## 关键文件

| 文件 | 职责 |
|---|---|
| `hooks/schema.py` | 契约：`HookEvent` / `HookContext` / `AdditionalContext` / `Block` |
| `hooks/dispatch.py` | 注册表 + dispatch 三模式 + `has_hooks` |
| `hooks/builtin.py` | 内置 `structured_output_stop_hook` |
| `hooks/protocol.py` | Shell hook 决策 JSON 协议 |
| `hooks/exec_shell.py` | Shell command wrapper |
| `hooks/config_loader.py` | 三级 hooks.json 加载 |
| `meta_message.py` | meta/reminder 标记 + `iter_current_turn` 轮窗口遍历器 |

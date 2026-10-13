# 权限系统使用指南

Lumi 内置了基于配置文件的工具权限管理系统。Agent 在执行工具调用（如执行 bash 命令、编辑文件）前会自动评估权限，决定直接放行还是弹出审批对话框。

---

## 快速开始

桌面端默认使用 auto 模式，由 AI 审批需要授权的调用；切换到 default 模式后，这些调用会弹出人工审批。你可以通过配置文件预先 allow 常用操作来减少审批频率，deny 规则在所有模式下均直接拒绝。

---

## 配置文件

权限配置使用 JSON 格式（支持 JSONC 注释），按优先级从低到高加载并合并：

| 优先级 | 路径 | 用途 |
|---|---|---|
| 1 | `~/.lumi/permissions.json` | 用户全局配置（跨项目） |
| 2 | `.lumi/permissions.json` | 项目共享配置（可提交到 Git） |
| 3 | `.lumi/permissions.local.json` | 项目本地配置（建议加入 .gitignore） |

高优先级配置中的同名工具规则会覆盖低优先级的规则，但 `deny` 规则不可被覆盖：任何一层（包括同一文件内）对同一表达式写的 `allow`/`ask` 都盖不掉已有的 `deny`。配置文件修改后自动热重载，无需重启。

### 配置结构

```jsonc
{
  // 权限规则
  "permissions": {
    "allow": [
      "read",              // 允许所有 read 操作
      "bash(npm *)",       // 允许 npm 相关命令
      "edit(src/**/*.py)"  // 允许编辑 src 下的 Python 文件
    ],
    "deny": [
      "bash(rm -rf *)"    // 禁止 rm -rf 命令
    ],
    "ask": [
      "bash(git push *)",  // git push 需要确认
      "bash(npm publish *)" // npm publish 需要确认
    ]
  }
}
```

---

## 权限类型

| 权限 | 行为 |
|---|---|
| `allow` | 直接放行，不弹出审批 |
| `deny` | 直接拒绝、不弹审批，拒绝原因回给模型让它换做法 |
| `ask` | 优先于 allow；特权模式直接执行，auto 模式交 AI，default / accept_edits 模式交人工 |

当同一个工具调用匹配多条规则时，取最严格结果：`deny` > `ask` > `allow`。

`ask` 适用于希望按当前审批模式确认的操作，如 `git push`、`npm publish`；如果需要人工确认，请使用 default / accept_edits 模式。

---

## 工具表达式语法

权限规则通过工具表达式匹配工具调用：

| 格式 | 说明 | 示例 |
|---|---|---|
| `tool_name` | 匹配该工具的所有调用 | `read`、`bash`、`cron` |
| `bash(pattern)` | 匹配命令内容，`*` 为通配符 | `bash(npm *)` 匹配所有 npm 命令 |
| `tool(path_pattern)` | 匹配文件路径（gitignore 风格） | `edit(src/**/*.py)` |

### 纯工具名 vs 带括号模式

- `read` — 无条件放行所有 read 调用，不检查路径
- `read(*)` / `read(**)` — 不带 `/` 前缀的模式在任意层级匹配，效果等同纯工具名（任意路径）
- `read(/*)` — 只匹配项目根下一层的文件；`read(/src/**)` 只匹配 `src` 子树

如果你想放行某个工具的全部操作，直接写工具名即可，不需要加 `(*)`。

### 命令模式（bash 工具）

- `*` 匹配任意字符序列
- `bash(npm *)` — 匹配 `npm install`、`npm run dev` 等
- `bash(git *)` — 匹配所有 git 命令
- 模式以 ` *` 结尾时，命令不带参数也能匹配（如 `bash(ls *)` 同时匹配 `ls` 和 `ls -la`）

### 路径模式（文件操作工具）

适用于 `read`、`write`、`edit` 等工具：

- `*` 匹配单层目录中的任意字符（不含 `/`）
- `**` 匹配零或多层目录
- `/` 前缀表示从项目根目录开始匹配；项目外的路径一律不命中（`/**` 也不例外）
- `edit(src/**/*.py)` — 匹配 src 下所有 Python 文件
- `read(*.md)` — 匹配任意目录下的 Markdown 文件

### 复合命令

bash 复合命令（如 `git add . && git push`）会被拆分为独立子命令逐个评估，取最严格结果：

- 任意子命令 deny → 整体 `deny`
- 任意子命令 ask → 整体 `ask`
- 任意子命令 unmatched → 整体 `unmatched`
- 全部子命令 allow → 整体 `allow`

引号内的分隔符不会被拆分。

---

## 文件路径与项目目录

项目目录决定相对路径、Shell 初始目录和项目配置。Lumi 不限制文件操作必须位于项目目录内；绝对路径、`~`、`..` 和符号链接都按实际目标解析，项目外操作按同一套权限规则和审批模式评估，实际访问仍受操作系统权限约束。

旧权限配置中的 `workspaces` 字段已不再使用，可直接删除。

---

## 审批对话框

当工具调用需要人工审批时，每个调用会生成独立的审批卡，展示工具名、逐项参数和命令风险提示，可单独选择：

| 选项 | 说明 |
|---|---|
| 允许 | 放行这个调用，不修改配置 |
| 拒绝 | 拒绝这个调用 |

点击允许或拒绝即提交当前调用的决定；允许后立即执行，无需等待其他审批。被拒的调用会附说明回给模型；只有本批全部未执行且存在人工拒绝时才结束本轮。用户点击停止仍会结束本轮。

要让某类操作以后免审批，在权限配置里预先写 allow 规则（见上文）。

---

## 统一权限规则

每个工具调用独立判断，优先级为 `deny > ask > allow > 未匹配`：

- `deny`：所有模式直接拒绝，不能通过审批覆盖。
- `allow`：所有模式直接执行，不交人工或 AI 审批。
- `ask`：特权模式直接执行，auto 模式交 AI，default / accept_edits 模式交人工。

只读工具、记忆写入、Shell / Git / SSH 配置和权限配置都遵守同一规则，没有隐式免审或强制人工审批名单。
同批调用独立审批后立即执行：一个被拒绝或等待审批，不影响其他已允许的调用。结果按原工具调用顺序写回；只有本批全部未执行且存在人工拒绝时才结束本轮，权限规则或 AI 拒绝会回给模型继续调整。用户点击停止仍会结束本轮。

内置默认 allow 规则为 `cron`、`artifacts`、`__structured_output__`（框架结构化输出工具），均可通过配置覆盖。

---

## 工具审批模式

Lumi 提供四种工具审批模式，控制工具调用何时需要人工审批：

| 模式 | 行为 | 启用方式 |
|---|---|---|
| `auto` | `deny` 拒绝，`allow` 放行，`ask` / 未匹配逐个交 AI 裁决 | 桌面默认；无人值守全局默认 |
| `default` | 权限引擎评估，`deny` 拒绝，`allow` 放行，`ask` / 未匹配逐个弹出审批 | 桌面输入栏审批模式切换 |
| `accept_edits` | `deny` 拒绝、`allow` 放行；未匹配的 `write`/`edit` 自动放行，显式 `ask` 仍需人工审批 | 桌面输入栏审批模式切换 |
| `privileged` | `deny` 拒绝；`ask`、`allow`、未匹配直接放行 | 桌面输入栏审批模式切换；`lumi -p` 加 `--privileged-danger` |

### accept_edits 模式

适合连续执行多个文件编辑的场景。文件编辑工具自动放行，但 `bash` 等具有副作用的命令仍需审批：

运行中也可以在输入框底栏切换审批模式，对当前运行轮后续的工具调用即时生效。

### 特权模式

桌面输入栏切换，或 `lumi -p` 加 `--privileged-danger`，跳过所有常规审批：

```bash
lumi --privileged-danger -p "执行所有迁移"
```

特权模式下只有 `deny` 规则会拒绝调用，`ask`、`allow` 和未匹配的调用均直接执行。

### 无人应答的执行

在「设置 → 通用 → 无人值守任务」选择全局审批模式，同一台机器的后台子代理、工作流、定时任务、`lumi -p` 与记忆整理（dream）共用。默认 `auto`（AI 审批），也可选 `privileged`（特权模式，跳过分类器）。没有每个定时任务的覆盖项；多台机器分别保存自己的全局设置。

设置保存在 `~/.lumi/lumi.json` 的 `settings.unattended_tool_mode` 字段，值为 `auto` 或 `privileged`。统一路由在没有审批通道时读取它；显式指定的 `auto` / `privileged` 保留，`lumi -p --privileged-danger` 仍可显式覆盖。`--accept-edits` 在无人值守入口也跟随全局设置。

这些入口均无人工审批通道：禁止规则和 AI 拒绝的调用直接拒绝；AI 审批失败也仅拒绝对应调用，提问直接取消，不会挂起等待。子代理一律不带 `ask` 工具。飞书保留渠道配置的 `auto` / `privileged` 模式，主代理和前台子代理均无人工审批通道，不显示审核按钮。

## Bash 命令安全警告

审批界面会对以下命令模式显示安全警告（仅提示，不阻断）：

| 模式 | 级别 | 说明 |
|---|---|---|
| `git push --force` | danger | 可能覆盖远程提交历史 |
| `git reset --hard` | danger | 会丢失未提交的本地更改 |
| `git clean -f` | danger | 会删除未跟踪的文件 |
| `curl ... \| sh` | danger | 从网络下载并直接执行脚本 |
| `chmod 777` | warning | 开放所有权限 |

---

## 默认规则

系统内置以下默认规则（可被用户配置覆盖）：

- `cron` → allow
- `artifacts` → allow（只呈现文件，不改动文件）
- `__structured_output__` → allow（框架结构化输出工具）

---

## 完整配置示例

```jsonc
{
  "permissions": {
    "allow": [
      "read",
      "bash(npm *)",
      "bash(git status)",
      "bash(git diff *)",
      "edit(src/**/*.py)",
      "write(src/**/*.py)"
    ],
    "deny": [
      "bash(rm -rf *)",
      "bash(sudo *)"
    ],
    "ask": [
      "bash(git push *)",
      "bash(npm publish *)"
    ]
  }
}
```

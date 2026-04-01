# 工具权限控制系统

Lumi 内置了基于配置文件的工具权限管理系统，支持 allow/deny 规则匹配、工作区边界保护和多级配置加载。权限系统在 Agent 执行工具调用前自动评估，决定是否需要人工审批。

---

## 配置文件

权限配置使用 JSON 格式（支持 JSONC 注释），按优先级从低到高加载并合并：

| 优先级 | 路径 | 用途 |
|---|---|---|
| 1 | `~/.lumi/permissions.json` | 用户全局配置 |
| 2 | `.lumi/permissions.json` | 项目共享配置（可提交到 Git） |
| 3 | `.lumi/permissions.local.json` | 项目本地配置（建议加入 .gitignore） |

高优先级配置中的同名工具规则会覆盖低优先级的规则。

### 配置结构

```jsonc
{
  // 额外授权的工作区目录（支持绝对路径和相对路径）
  "workspaces": [],

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

## 工具表达式语法

权限规则通过工具表达式匹配工具调用：

| 格式 | 说明 | 示例 |
|---|---|---|
| `tool_name` | 匹配该工具的所有调用，不限制任何参数 | `read`、`bash`、`cron` |
| `bash(pattern)` | 匹配命令内容，`*` 为通配符 | `bash(npm *)` 匹配所有 npm 命令 |
| `tool(path_pattern)` | 匹配文件路径（gitignore 风格） | `edit(src/**/*.py)` |

> **纯工具名 vs 带括号通配符的区别：**
>
> - `read` — 规则匹配层面无条件放行所有 read 调用，不检查路径参数
> - `read(*)` — 只允许读取单层路径匹配 `*` 的文件（`*` 不含 `/`，即只匹配当前目录下的文件）
> - `read(**)` — 允许读取任意路径的文件（`**` 匹配零或多层目录）
>
> 简而言之，纯工具名跳过模式匹配直接放行；带括号的表达式会进入模式匹配逻辑，按工具类型分别匹配命令内容或文件路径。如果你想放行某个工具的全部操作，直接写工具名即可，不需要加 `(*)`。
>
> **注意：** 工作区边界保护仅对写操作（`write`、`edit`）生效。只读工具（`read`、`glob`、`grep`）不受工作区边界限制，可以读取任意路径的文件。

### 命令模式（bash 工具）

- `*` 匹配任意字符序列
- `bash(npm *)` → 匹配 `npm install`、`npm run dev` 等
- `bash(git *)` → 匹配所有 git 命令

### 路径模式（文件操作工具）

适用于 `read`、`write`、`edit`、`glob`、`grep` 等工具：

- `*` 匹配单层目录中的任意字符（不含 `/`）
- `**` 匹配零或多层目录
- `/` 前缀表示从项目根目录开始匹配
- `edit(src/**/*.py)` → 匹配 src 下所有 Python 文件
- `read(*.md)` → 匹配任意目录下的 Markdown 文件

---

## 评估流程

权限引擎按以下顺序评估每个工具调用：

1. `BYPASS_TOOLS`（如 `ask`、`read`、`glob`、`grep`、`todos`、`skill`、`agent`）始终直接执行，不经过权限评估
2. `tool_mode` 为 `privileged` 时跳过所有审批（bypass-immune 检查除外，见下文）
3. 遍历所有规则，取最严格的匹配结果：`deny` > `ask` > `allow` > `unmatched`
4. 对 bash 复合命令（含 `&&`、`||`、`;`、`|`），拆分后逐个子命令评估，取最严格结果

### 权限类型

| 权限 | 说明 |
|---|---|
| `allow` | 直接放行，不弹出审批 |
| `deny` | 标记为危险，审批界面显示警告 |
| `ask` | 需要确认，即使存在更宽泛的 allow 规则也会弹出审批 |

`ask` 规则适用于"允许但需确认"的场景，如 `git push`、`npm publish` 等不可逆操作。`ask` 的优先级高于 `allow` 但低于 `deny`：

- `deny` + `ask` → `deny`（deny 始终最高优先级）
- `ask` + `allow` → `ask`（ask 覆盖 allow）
- `ask` + `unmatched` → `ask`

### 复合命令评估

bash 复合命令（如 `git add . && git push origin main`）会被拆分为独立子命令，逐个评估后取最严格结果：

- 任意子命令 deny → 整体 `deny`
- 任意子命令 ask → 整体 `ask`
- 任意子命令 unmatched → 整体 `unmatched`
- 全部子命令 allow → 整体 `allow`

引号内的分隔符不会被拆分（如 `bash -c "cmd1 && cmd2"` 作为单条命令评估）。

### 审批模式与权限决策的交互

| tool_mode | 全部 allow | 含 deny/ask/unmatched |
|---|---|---|
| `privileged` | 直接执行 | 直接执行（bypass-immune 除外） |
| `auto` | 直接执行 | 弹出权限审批 |

---

## 工作区边界保护

权限系统对写操作工具（`write`、`edit`）检查文件路径是否在授权的工作区范围内。只读工具（`read`、`glob`、`grep`）不受此限制。默认工作区为项目根目录，可通过 `workspaces` 字段扩展：

```jsonc
{
  "workspaces": [
    "/home/user/shared-libs",
    "../other-project"
  ]
}
```

超出边界的操作会在审批界面显示警告，用户可选择临时授权或永久添加到工作区列表。

---

## 审批选项

当工具调用触发审批时，根据权限决策提供不同选项：

| 选项 | 说明 |
|---|---|
| 允许执行这一次 | 仅本次放行，不修改配置 |
| 始终允许（精确匹配） | 将精确的工具表达式写入 `permissions.local.json` |
| 始终允许（模式匹配） | 将宽泛模式写入配置（如 `bash(npm *)` 代替 `bash(npm install)`) |
| 拒绝 | 拒绝执行 |
| Esc | 中断当前工具调用 |

选择"始终允许"后，规则会自动持久化到 `.lumi/permissions.local.json`，后续相同操作将自动放行。

---

## 默认规则

系统内置以下默认规则（可被用户配置覆盖）：

- `cron` → allow（定时任务管理工具默认允许）

---

## 特权模式

通过 CLI 启动参数 `--privileged-danger` 启用特权模式，跳过所有审批。定时任务（cron）执行时也会自动使用此模式。

```bash
lumi --privileged-danger
lumi --privileged-danger -p "执行所有迁移"
lumi web-server --privileged-danger
```

特权模式下状态栏显示 `▶▶ privileged ⚠`，`Shift+Tab` 不可用。

> 注意：特权模式下所有工具调用将直接执行，请谨慎使用。

### Bypass-immune 安全检查

即使在特权模式下，以下操作仍然需要人工审批（不可跳过）：

| 类别 | 受保护目标 |
|---|---|
| Shell 配置 | `~/.bashrc`、`~/.zshrc`、`~/.bash_profile`、`~/.zprofile`、`~/.profile`、`~/.login` |
| Git 配置 | `~/.gitconfig` |
| SSH/GPG | `~/.ssh/*`、`~/.gnupg/*` |
| 项目权限配置 | `.lumi/permissions.json`、`.lumi/permissions.local.json`、`.git/config` |
| 危险 bash 模式 | `curl ... \| sh`、`wget ... \| bash` |

检查范围包括 `write`/`edit` 工具的目标路径以及 bash 命令中的写入操作（重定向、`tee`、`sed -i`、`cp`、`mv`）。只读操作不触发此检查。

### Bash 命令安全警告

审批界面会对以下 bash 命令模式显示安全警告（不阻断执行，仅辅助决策）：

| 模式 | 级别 | 说明 |
|---|---|---|
| `git push --force` | danger | 可能覆盖远程提交历史 |
| `git reset --hard` | danger | 会丢失未提交的本地更改 |
| `git clean -f` | danger | 会删除未跟踪的文件 |
| `curl ... \| sh` | danger | 从网络下载并直接执行脚本 |
| `chmod 777` | warning | 开放所有权限 |

### 特权工具列表

某些工具被设计为始终绕过审批（即使在 `auto` 模式下），称为"特权工具"。这些工具通常是低风险操作或用户交互类工具，例如：

- `ask` — 向用户提问（自带中断机制）
- `read`、`glob`、`grep` — 文件系统只读操作
- `todos` — 仅更新会话内部状态，无文件系统副作用
- `skill` — 读取技能提示词，只读操作
- `agent` — 子 agent 调度，权限由子 agent 自身的工具调用独立评估

完整列表见 `lumi/agents/tools/permissions/models.py` 中的 `BYPASS_TOOLS` 常量。

特权工具的判定优先级高于 `tool_mode`，因此无论在何种模式下都会直接执行。

---

## 完整配置示例

```jsonc
{
  "workspaces": [],
  "permissions": {
    "allow": [
      "read",
      "glob",
      "grep",
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

# 权限系统使用指南

Lumi 内置了基于配置文件的工具权限管理系统。Agent 在执行工具调用（如执行 bash 命令、编辑文件）前会自动评估权限，决定直接放行还是弹出审批对话框。

---

## 快速开始

默认情况下，Lumi 会对大多数有副作用的操作（bash 命令、文件写入）弹出审批。你可以通过配置文件预先 allow 常用操作来减少审批频率，也可以在审批对话框中选择「始终允许」来动态积累规则。

---

## 配置文件

权限配置使用 JSON 格式（支持 JSONC 注释），按优先级从低到高加载并合并：

| 优先级 | 路径 | 用途 |
|---|---|---|
| 1 | `~/.lumi/permissions.json` | 用户全局配置（跨项目） |
| 2 | `.lumi/permissions.json` | 项目共享配置（可提交到 Git） |
| 3 | `.lumi/permissions.local.json` | 项目本地配置（建议加入 .gitignore） |

高优先级配置中的同名工具规则会覆盖低优先级的规则。配置文件修改后自动热重载，无需重启。

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

## 权限类型

| 权限 | 行为 |
|---|---|
| `allow` | 直接放行，不弹出审批 |
| `deny` | 标记为危险，审批界面显示警告并自动拒绝 |
| `ask` | 弹出审批，即使存在更宽泛的 allow 规则 |

当同一个工具调用匹配多条规则时，取最严格结果：`deny` > `ask` > `allow`。

`ask` 适用于"允许但需确认"的场景，如 `git push`、`npm publish` 等不可逆操作。

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
- `read(*)` — 只允许读取当前目录下的文件（`*` 不含 `/`）
- `read(**)` — 允许读取任意路径的文件

如果你想放行某个工具的全部操作，直接写工具名即可，不需要加 `(*)`。

### 命令模式（bash 工具）

- `*` 匹配任意字符序列
- `bash(npm *)` — 匹配 `npm install`、`npm run dev` 等
- `bash(git *)` — 匹配所有 git 命令
- 模式以 ` *` 结尾时，命令不带参数也能匹配（如 `bash(ls *)` 同时匹配 `ls` 和 `ls -la`）

### 路径模式（文件操作工具）

适用于 `read`、`write`、`edit`、`glob`、`grep` 等工具：

- `*` 匹配单层目录中的任意字符（不含 `/`）
- `**` 匹配零或多层目录
- `/` 前缀表示从项目根目录开始匹配
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

## 工作区边界保护

写操作工具（`write`、`edit`、`bash` 中的路径操作命令）会检查目标路径是否在授权的工作区范围内。默认工作区为项目根目录，可通过 `workspaces` 字段扩展：

```jsonc
{
  "workspaces": [
    "/home/user/shared-libs",
    "../other-project"
  ]
}
```

超出边界的操作会在审批界面显示越界警告。只读工具（`read`、`glob`、`grep`）不受工作区边界限制。

---

## 审批对话框

当工具调用触发审批时，可选操作：

| 选项 | 说明 |
|---|---|
| 允许执行这一次 | 仅本次放行，不修改配置 |
| 始终允许（精确匹配） | 将精确表达式写入本地配置（如 `bash(npm install)`) |
| 始终允许（模式匹配） | 将宽泛模式写入本地配置（如 `bash(npm *)`) |
| 拒绝 | 拒绝执行 |
| Esc | 中断当前工具调用 |

选择「始终允许」后，规则会自动持久化到 `.lumi/permissions.local.json`，后续相同操作自动放行。

---

## 自动绕过审批的工具

以下工具始终直接执行，无需审批：

| 工具 | 原因 |
|---|---|
| `ask` | 向用户提问，自带中断机制 |
| `read`、`glob`、`grep` | 只读操作，无副作用 |
| `todos` | 仅修改会话内部状态 |
| `skill` | 读取技能提示词，只读 |
| `agent` | 子 agent 调度，权限由子 agent 自身独立评估 |
| `cron` | 定时任务管理（默认 allow 规则） |
| `EnterPlanMode`、`ExitPlanMode` | 模式切换 |

此外，bash 中的只读命令（如 `ls`、`cat`、`git status`、`grep` 等）也会自动绕过审批。

---

## 特权模式

通过 `--privileged-danger` 启用特权模式，跳过所有常规审批：

```bash
lumi --privileged-danger
lumi --privileged-danger -p "执行所有迁移"
```

特权模式下状态栏显示 `▶▶ privileged ⚠`。

> **注意**：特权模式下，`ask` 规则仍会弹出审批，`deny` 规则仍会触发自动拒绝。只有 `unmatched` 和 `allow` 的工具调用会直接放行。

### 即使特权模式也不可跳过的操作

以下操作无论任何模式都需要人工审批（bypass-immune）：

| 类别 | 受保护目标 |
|---|---|
| Shell 配置 | `~/.bashrc`、`~/.zshrc`、`~/.bash_profile`、`~/.zprofile`、`~/.profile`、`~/.login` |
| Git 配置 | `~/.gitconfig` |
| SSH/GPG | `~/.ssh/*`、`~/.gnupg/*` |
| 项目权限配置 | `.lumi/permissions.json`、`.lumi/permissions.local.json`、`.git/config` |
| 危险 bash 模式 | `curl ... \| sh`、`wget ... \| bash` |

仅检查写入操作，读取不受限。

---

## 执行模式

除了 `auto`（默认）和 `privileged`（特权）外，Lumi 还支持以下执行模式：

| 模式 | 行为 | 切换方式 |
|---|---|---|
| `plan` | 仅允许只读操作和写入 `.lumi/plans/*.md` | `Shift+Tab` 或 `EnterPlanMode` 工具 |
| `readonly` | 仅允许只读操作，完全禁止写入 | 编程接口设置 |

Plan 模式下，Agent 可以阅读代码并制定计划（写入计划文件），但不能修改项目代码或执行有副作用的命令。

---

## 临时规则

通过 CLI `--allow` 参数添加仅当前会话有效的 allow 规则，不写入配置文件：

```bash
lumi --allow "bash(npm *)" --allow "edit"
```

---

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

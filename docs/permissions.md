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
  // 特权模式：跳过所有审批检查
  "privileged": false,

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
    ]
  }
}
```

---

## 工具表达式语法

权限规则通过工具表达式匹配工具调用：

| 格式 | 说明 | 示例 |
|---|---|---|
| `tool_name` | 匹配该工具的所有调用 | `read`、`bash`、`cron` |
| `bash(pattern)` | 匹配命令内容，`*` 为通配符 | `bash(npm *)` 匹配所有 npm 命令 |
| `tool(path_pattern)` | 匹配文件路径（gitignore 风格） | `edit(src/**/*.py)` |

### 命令模式（bash 工具）

- `*` 匹配任意字符序列
- `bash(npm *)` → 匹配 `npm install`、`npm run dev` 等
- `bash(git *)` → 匹配所有 git 命令

### 路径模式（文件操作工具）

适用于 `read`、`write`、`edit`、`ls`、`glob`、`grep` 等工具：

- `*` 匹配单层目录中的任意字符（不含 `/`）
- `**` 匹配零或多层目录
- `/` 前缀表示从项目根目录开始匹配
- `edit(src/**/*.py)` → 匹配 src 下所有 Python 文件
- `read(*.md)` → 匹配任意目录下的 Markdown 文件

---

## 评估流程

权限引擎按以下顺序评估每个工具调用：

1. `BYPASS_TOOLS`（如 `ask`）始终直接执行，不经过权限评估
2. `privileged` 模式（配置或环境变量 `LUMI_PRIVILEGED=true`）跳过所有审批
3. 先匹配 deny 规则 → 命中则返回 `deny`
4. 再匹配 allow 规则 → 命中则返回 `allow`
5. 未匹配任何规则 → 返回 `unmatched`

### 审批模式与权限决策的交互

| tool_mode | 全部 allow | 含 deny/unmatched |
|---|---|---|
| `privileged` | 直接执行 | 直接执行 |
| `auto` | 直接执行 | 弹出权限审批 |
| `approve`/`supervised` | 弹出执行确认 | 弹出合并审批（确认 + 权限选项） |

---

## 工作区边界保护

权限系统会检查工具调用涉及的文件路径是否在授权的工作区范围内。默认工作区为项目根目录，可通过 `workspaces` 字段扩展：

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

两种方式启用特权模式（跳过所有审批）：

1. 配置文件：`"privileged": true`
2. 环境变量：`LUMI_PRIVILEGED=true`
3. TUI 中切换 tool_mode 为 `privileged`

> 注意：特权模式下所有工具调用将直接执行，请谨慎使用。

---

## 完整配置示例

```jsonc
{
  "privileged": false,
  "workspaces": [],
  "permissions": {
    "allow": [
      "read",
      "ls",
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
    ]
  }
}
```

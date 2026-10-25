# 权限系统架构

权限系统位于 `lumi/agents/permissions/`，负责在 Agent 执行工具调用前逐个评估权限。本文档面向开发者，介绍内部架构、数据流和扩展方式。

用户使用指南见 [`docs/guides/permissions.md`](../guides/permissions.md)。

---

## 模块总览

```
lumi/agents/permissions/
├── models.py         # 数据模型：枚举、frozen dataclass、常量
├── engine.py         # 权限引擎：协调配置加载、规则匹配
├── config_loader.py  # 三级配置加载、JSONC 解析、合并
├── matcher.py        # 规则匹配器：命令模式、路径模式
├── routing.py        # route_decision：单个工具调用的权限决策纯函数
└── validators.py     # Bash 命令安全警告（非阻断）

lumi/agents/runtime/workspace.py # 会话项目目录上下文与路径解析
lumi/agents/tools/capability.py    # 文件编辑类型判定 + bash 复合命令拆分
lumi/agents/tools/shell_syntax.py  # bash 保守词法切分（规则匹配与后台操作符检测共用）
```

---

## 统一判定与逐个执行

`routing.route_decision(tool_call, tool_mode, engine)` 独立判断一个调用：

| 规则 | default | accept_edits | privileged | auto |
|---|---|---|---|---|
| deny | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| allow | 执行 | 执行 | 执行 | 执行 |
| ask | 人工审批 | 人工审批 | 执行 | AI 审批 |
| 未匹配 | 人工审批 | write/edit 执行，其余人工审批 | 执行 | AI 审批 |

没有只读免审、记忆写入免审或受保护路径的强制审批例外。权限评估失败只拒绝该调用。
`capability.py` 保留文件编辑类型判定和 `split_compound_command`，后者用同一套 Shell 词法切分让 bash 子命令分别匹配配置规则；同一个 bash 调用仍以最严格的匹配结果整体裁决。

---

## 核心数据模型（models.py）

所有模型使用 `@dataclass(frozen=True)` 保证不可变：

```python
class Permission(Enum):         # allow | deny | ask
class PermissionDecision(Enum): # allow | deny | ask | unmatched

@dataclass(frozen=True)
class PermissionRule:           # tool: str, permission: Permission
class PermissionConfig:         # permissions: tuple[PermissionRule]
```

常量：
- `DEFAULT_RULES`：`agent_list`、`cron`、`artifacts`、`__structured_output__` 四条 ALLOW；具体含义见 [默认规则](../guides/permissions.md#默认规则)。

---

## 权限引擎（engine.py）

`PermissionEngine` 是权限系统的核心入口，协调配置加载和规则匹配。

### 初始化

```python
engine = PermissionEngine(project_dir=Path("."), user_config_dir=Path("~/.lumi"))
```

1. 通过 `ConfigLoader` 加载三级配置并合并
2. 没有用户规则时保留内置 `DEFAULT_RULES`；其余调用返回 `unmatched`

`rebase(project_dir)` 切换项目根目录：重载新目录的权限配置。

### 评估流程

`evaluate(tool_name, tool_args) -> PermissionDecision`:

1. bash 复合命令（含 `&&`、`||`、`;`、`|`）→ `split_compound_command()` 拆分后由 `_evaluate_compound()` 逐个子命令评估
2. 单条命令 → `_evaluate_single()` 单次遍历规则列表
3. 取最严格匹配结果：`deny(0) > ask(1) > allow(2) > unmatched(3)`
4. 命中 deny 立即短路返回

```python
_STRICTNESS = {Permission.DENY: 0, Permission.ASK: 1, Permission.ALLOW: 2}
```

复合命令严格度：`ANY deny → DENY; ANY ask → ASK; ANY unmatched → UNMATCHED; ALL allow → ALLOW`。

### 项目与热重载

- `project_dir` — 引擎绑定的项目根，用于发现项目权限配置和解析路径规则。
- `rebase(project_dir)` — 切换项目并重新加载配置。
- `reload()` — 文件 mtime 变化后重载；加载异常时保留旧配置。

项目目录用于确定配置和相对路径，不限制工具可访问的文件位置。写入项目外文件仍按同一套规则、审批模式评估。

---

## 配置加载（config_loader.py）

`ConfigLoader` 管理三级配置的加载与合并：

```
优先级（低→高）：
  ~/.lumi/permissions.json                # 用户全局
  {project}/.lumi/permissions.json        # 项目共享
  {project}/.lumi/permissions.local.json  # 项目本地
```

配置文件格式（支持 JSONC，由 `lumi/utils/jsonc.parse_jsonc` 解析）：

```jsonc
{
  "permissions": {
    "allow": ["read", "bash(npm *)"],
    "deny":  ["bash(rm -rf *)"],
    "ask":   ["bash(git push *)"]
  }
}
```

### 合并策略

`_merge_configs(configs)` — 按优先级从低到高遍历，同一工具表达式的规则以最后出现的为准（后覆盖前），但 DENY 例外：同一表达式已有 DENY 时后来的 ALLOW/ASK 一律跳过，deny 规则不可被其他层（或同文件内）更低严格度的规则覆盖。最后追加 `DEFAULT_RULES` 中未被覆盖的规则。

### 持久化

引擎只读取三层 JSON 中的权限规则，不写配置。单次审批不修改规则。旧配置里的 `workspaces` 字段会被忽略，可删除。

### 热重载

`needs_reload()` — 基于 mtime 检测文件变更（含文件被删除）。`PermissionEngine.reload()` 在 `ToolExecutor` 每批调用开始时执行，仅在文件变更时实际重新加载。加载异常时保留旧配置。

---

## 规则匹配器（matcher.py）

`RuleMatcher` 提供纯函数式的匹配逻辑：

### 工具表达式解析

`parse_tool_expression("bash(npm *)")` → `("bash", "npm *")`；纯工具名 `"read"` → `("read", None)`。

### 匹配分派

`match_rule(rule, tool_name, tool_args)`:
1. 工具名不匹配 → False
2. 无模式（纯工具名）→ True
3. bash 工具（`COMMAND_TOOLS`）→ `match_command_pattern()`，命令参数键 `COMMAND_ARG_KEYS`（`command` / `cmd`）
4. 路径工具（`PATH_TOOLS`：read/write/edit）→ `match_path_pattern()`，路径参数键 `PATH_ARG_KEYS`（`file_path` / `path`）
5. 其他工具（如 MCP 工具）→ 尝试将模式与每个字符串参数值匹配

### 命令模式匹配

`match_command_pattern(pattern, command)`:
- `*` → `.*`（正则），全匹配，DOTALL 支持多行（heredoc）
- 模式以 ` *` 结尾且仅一个通配符 → 尾部空格+参数变为可选（`ls *` 匹配 `ls` 和 `ls -la`）

### 路径模式匹配

`match_path_pattern(pattern, file_path, project_dir)`:
- gitignore 风格：`*` 匹配单层不含 `/`，`**` 匹配零或多层
- 路径先按工具执行同一口径归一（展开 `~`、消掉 `..`）；项目内路径转为相对项目根，项目外路径保持绝对
- `/` 前缀 → 锚定项目根匹配（fullmatch），项目外路径一律不命中（含 `/**`）；否则任意目录层级匹配（项目外路径以绝对路径参与，`**/.env` 这类 deny 照样命中）

---

## Bash 安全警告（validators.py）

`validate_bash_command(command) -> list[SafetyWarning]`

非阻断的安全提示，在审批 UI 中展示。基于正则匹配危险模式（`_DANGER_PATTERNS`：force push、`reset --hard`、`clean -f`、curl/wget 管道到 shell、`chmod 777`、写入块设备等）。`SafetyWarning.level` 分 `warning` 和 `danger`。

---

## 项目目录上下文（runtime/workspace.py）

`set_project_source()` 在每轮 run 起点注入项目目录回调，`get_project_directory()` 读取该目录；直接调用 Graph 时从运行时 context 的权限引擎或 `project_dir` 取项目，无 run 上下文时退回进程 cwd。bridge 注入本会话的 `workspace_dir` 来源，dream 注入自己的项目目录。contextvar 隔离并发 run，子任务继承上下文，回调可反映项目切换。

`resolve_tool_path()` 统一展开 `~`、归一化 `..` 和符号链接，相对路径基于项目根，绝对路径可指向任意目录。文件工具读取、写入和编辑都用同一解析口径；Bash 用项目目录作为初始 cwd。路径解析不做范围校验，实际访问受操作系统权限约束。

子代理另经 `shell_session.run_with_shell` 隔离各自的 shell 会话（`cd`/env 不串父/兄弟、用完回收），见 [desktop.md](desktop.md)。

---

## Graph 节点集成

`is_use_tool()` 在无 tool_calls 时返回 `OnAgentStop`，有调用时统一进入 `ToolExecutor`。
权限决策和实际执行都在 `tool_executor()` 内按调用并发进行：

1. 批次开始时 `engine.reload()` 热重载配置。
2. 每个调用读取当前有效审批模式，经 `route_decision()` 判定。
3. DENY / 评估错误只为该调用生成拒绝 ToolMessage；ALLOW / 特权放行立即通过 ToolNode 执行。
4. AI 审批每次只收到一个调用；approve 后立即执行，reject / 调用失败仅拒绝当前调用。
5. 人工审批仅发送需要审批的调用，独立等待 Broker；拒绝不影响兄弟调用。
6. 汇总结果：按原调用顺序排列全部 ToolMessage，然后追加图片回灌与 hook reminder，保证 provider 配对协议。

`human_approval()` 与 `auto_classify()` 是执行器调用的审批辅助函数，不再是整批路由节点。只有全部未执行且存在人工拒绝时才结束本轮；规则 / AI 拒绝继续回模型。用户停止通过 Broker 的 `stop` 标记结束本轮，即使已有其他调用执行，也不继续调用模型。

图保留旧 `HumanApproval` / `AutoClassify` 节点名作为新执行器的别名，仅用于读取和恢复旧 checkpoint；新调用统一进入 `ToolExecutor`。

`effective_tool_mode()` 统一选择无人值守模式：无审批通道且模式为 default / accept_edits 时读取全局设置，显式 auto / privileged 保留。持久 Lumi 子代理保存委派时的模式快照，不接人工 Broker；Workflow 临时执行单元同样沿用无人值守规则。`allow` 调用无需任何审批通道，人工审批无通道时只拒绝当前调用。持久子代理的问题转交与外部执行器权限见 [子代理架构](agents.md)。

PreToolUse / PostToolUse hooks 保持其既有语义；非阻断 Bash 告警继续在审批 UI 中展示。

## 扩展指南

新增工具无需登记只读豁免。需要默认允许的工具可以在 `models.DEFAULT_RULES` 中定义显式 ALLOW，用户配置仍可覆盖。危险命令的提示在 `validators.py` 中添加，不改变权限裁决。

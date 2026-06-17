# 权限系统架构

权限系统位于 `lumi/agents/permissions/`，负责在 Agent 执行工具调用前评估权限、检查工作区边界、保护敏感文件。本文档面向开发者，介绍内部架构、数据流和扩展方式。

用户使用指南见 [`docs/guides/permissions.md`](../guides/permissions.md)。

---

## 模块总览

```
lumi/agents/permissions/
├── models.py         # 数据模型：枚举、frozen dataclass、常量
├── engine.py         # 权限引擎：协调配置加载、规则匹配、边界检查
├── config_loader.py  # 三级配置加载、JSONC 解析、合并、持久化
├── matcher.py        # 规则匹配器：命令模式、路径模式、复合命令拆分
├── boundary.py       # 工作区边界检查器：路径提取与边界判定
├── safety.py         # Bypass-immune 安全检查：受保护文件/命令检测
├── validators.py     # Bash 命令安全警告（非阻断）
├── mode_policy.py    # 执行模式策略（plan/readonly）
└── workspace.py      # 授权路径管理（全局状态，供 filesystem provider 使用）

lumi/agents/tools/capability.py  # 工具副作用声明（ToolEffect）+ bash 只读判断
```

---

## 三层工具限制机制

权限系统采用三层架构，各层职责独立：

```
Layer 1: 工具能力声明 (capability.py)
  ↓ 每个工具声明副作用类型 → 决定是否跳过审批
Layer 2: 执行模式策略 (mode_policy.py)
  ↓ plan/readonly 模式下拦截不允许的操作
Layer 3: 权限引擎 (engine.py)
  ↓ 规则匹配 + 工作区边界 → allow/deny/ask/unmatched
```

### Layer 1: ToolEffect（capability.py）

每个工具通过 `ToolEffect` 位标志声明自身副作用：

```python
class ToolEffect(Flag):
    NONE = 0           # 纯只读：read, glob, grep, skill
    FILE_WRITE = auto() # 写文件：write, edit
    SHELL_EXEC = auto() # 执行命令：非只读 bash
    STATE_MUTATE = auto() # 修改会话状态：todos, cron
    INTERRUPT = auto()   # 中断等待输入：ask, ExitPlanMode
```

`BYPASS_EFFECTS = NONE | INTERRUPT | STATE_MUTATE`：副作用全部在此集合内的工具跳过审批。

`should_bypass_approval(tool_name, tool_args)` 是对原 `BYPASS_TOOLS` 硬编码集合的语义化替代。bash 工具通过 `is_readonly_command()` 动态判断 — 白名单匹配已知只读命令前缀（如 `ls`、`cat`、`git status`），未识别命令默认视为非只读（fail-closed）。

### Layer 2: ModePolicy（mode_policy.py）

执行模式策略守卫，根据当前模式（`execution_mode` state 字段）限制工具调用：

```python
@dataclass(frozen=True)
class ModePolicy:
    name: str                              # "plan", "readonly"
    label: str                             # 拒绝消息中显示
    allowed_effects: ToolEffect            # 无条件放行的效果集合
    path_filter: Callable[[str], bool] | None  # FILE_WRITE 路径白名单
```

内置策略：

| 模式 | allowed_effects | path_filter |
|---|---|---|
| `plan` | NONE \| INTERRUPT \| STATE_MUTATE | `.lumi/plans/*.md` |
| `readonly` | NONE \| INTERRUPT | None（禁止所有写入） |
| `normal` | 无策略（policy=None），走后续权限引擎 | — |

`check_policy()` 检查工具调用是否被模式策略允许。`filter_tools_for_mode()` 在子 Agent 创建时静态过滤工具列表（Layer 3）。

扩展方式：`register_policy("my_mode", ModePolicy(...))` 注册自定义模式。

### Layer 3: PermissionEngine（engine.py）

规则匹配 + 工作区边界检查，详见下文。

---

## 核心数据模型（models.py）

所有模型使用 `@dataclass(frozen=True)` 保证不可变：

```python
class Permission(Enum):        # allow | deny | ask
class PermissionDecision(Enum): # allow | deny | ask | unmatched

@dataclass(frozen=True)
class PermissionRule:           # tool: str, permission: Permission
class PermissionConfig:         # workspaces: tuple, permissions: tuple[PermissionRule]
class ToolCallInfo:             # name: str, args: dict（批量评估用）
class ApprovalOption:           # key, label, tool_expr（审批 UI 选项）
class ApprovalRequest:          # 传递给 LangGraph interrupt 的审批请求
```

常量：
- `BYPASS_TOOLS`：兼容性保留，新代码应使用 `capability.should_bypass_approval()`
- `DEFAULT_RULES`：`(PermissionRule(tool="cron", permission=Permission.ALLOW),)`

---

## 权限引擎（engine.py）

`PermissionEngine` 是权限系统的核心入口，协调配置加载、规则匹配和边界检查。

### 初始化

```python
engine = PermissionEngine(project_dir=Path("."), user_config_dir=Path("~/.lumi"))
```

1. 通过 `ConfigLoader` 加载三级配置并合并
2. 构建 `WorkspaceBoundary` 并同步到 `workspace.py` 的全局授权目录
3. 配置加载失败时回退到无规则状态（所有调用返回 `unmatched`）

### 评估流程

`evaluate(tool_name, tool_args) -> PermissionDecision`:

1. bash 复合命令（含 `&&`、`||`、`;`、`|`）→ `_evaluate_compound()` 拆分后逐个评估
2. 单条命令 → `_evaluate_single()` 单次遍历规则列表
3. 取最严格匹配结果：`deny(0) > ask(1) > allow(2) > unmatched(3)`
4. 命中 deny 立即短路返回

```python
_STRICTNESS = {Permission.DENY: 0, Permission.ASK: 1, Permission.ALLOW: 2}
```

复合命令严格度：`ANY deny → DENY; ANY ask → ASK; ANY unmatched → UNMATCHED; ALL allow → ALLOW`。

### 边界检查

`check_workspace_boundary(tool_name, tool_args) -> bool`:

1. `WorkspaceBoundary.extract_paths_from_tool_call()` 从工具参数提取路径：标量键 `_PATH_ARG_KEYS`（`file_path` / `path`）取字符串值，列表键 `_PATH_LIST_ARG_KEYS`（`filepaths`，如 `present_files`）逐项提取；新增带路径参数的工具时须把对应键名登记进来，否则不参与边界检查
2. 相对路径基于项目目录解析
3. 逐个检查是否在任一工作区目录下
4. 无法提取路径时视为边界内（不阻断）；解析异常时保守拒绝

### 动态规则管理

- `add_allow_rule(tool_expr)` — 持久化到 `permissions.local.json`（审批对话框「始终允许」触发）
- `add_workspace(directory)` — 持久化并重建边界检查器
- `add_ephemeral_rules(allow_exprs)` — 仅内存，不持久化（CLI `--allow` 参数）
- `reload()` — 检查文件 mtime 变更后重新加载

---

## 配置加载（config_loader.py）

`ConfigLoader` 管理三级配置的加载与合并：

```
优先级（低→高）：
  ~/.lumi/permissions.json         # 用户全局
  {project}/.lumi/permissions.json  # 项目共享
  {project}/.lumi/permissions.local.json  # 项目本地
```

### 合并策略

`_merge_configs(configs)` — 按优先级从低到高遍历，同一工具表达式的规则以最后出现的为准（后覆盖前）。最后追加 `DEFAULT_RULES` 中未被覆盖的规则。workspaces 取并集并去重。

### 持久化

`save_local(config)` — 原子写入（tmpfile + rename），避免写入中途被读取到半成品。

### 热重载

`needs_reload()` — 基于 mtime 检测文件变更。`PermissionEngine.reload()` 在每次 `is_use_tool()` 路由时调用，仅在文件变更时实际重新加载。重建边界检查器失败时回滚到旧配置。

---

## 规则匹配器（matcher.py）

`RuleMatcher` 提供纯函数式的匹配逻辑：

### 工具表达式解析

`parse_tool_expression("bash(npm *)")` → `("bash", "npm *")`

### 匹配分派

`match_rule(rule, tool_name, tool_args)`:
1. 工具名不匹配 → False
2. 无模式（纯工具名）→ True
3. bash 工具 → `match_command_pattern()`
4. 路径工具（read/write/edit/glob/grep）→ `match_path_pattern()`
5. 其他工具 → 尝试将模式与每个字符串参数值匹配

### 命令模式匹配

`match_command_pattern(pattern, command)`:
- `*` → `.*`（正则），全匹配，DOTALL 支持多行（heredoc）
- 模式以 ` *` 结尾且仅一个通配符 → 尾部空格+参数变为可选（`ls *` 匹配 `ls` 和 `ls -la`）

### 路径模式匹配

`match_path_pattern(pattern, file_path, project_dir)`:
- gitignore 风格：`*` 匹配单层不含 `/`，`**` 匹配零或多层
- `/` 前缀 → 从项目根匹配（fullmatch），否则任意目录层级匹配
- 绝对路径先转为相对于项目根的相对路径

### 复合命令拆分

`split_compound_command(command)`:
- 字符级状态机，按 `&&`、`||`、`;`、`|`、`&` 拆分
- 正确处理单引号、双引号、反斜杠转义
- 引号内的分隔符不拆分

### 表达式构造

供审批 UI 生成「始终允许」选项：
- `build_exact_expr("bash", {"command": "npm install"})` → `"bash(npm install)"`
- `build_pattern_expr("bash", {"command": "npm install"})` → `"bash(npm *)"`
- `build_pattern_expr("edit", {"file_path": "src/main.py"})` → `"edit(**/*.py)"`

---

## 工作区边界检查（boundary.py）

`WorkspaceBoundary` 从工具调用中提取路径并检查边界：

### 路径提取

- 文件工具：从 `file_path` / `path` 参数直接提取
- bash 工具：解析命令字符串，识别已知路径操作命令（`ls`、`cp`、`mv`、`rm`、`mkdir` 等），提取非标志参数
  - 跳过 `sudo` 和 env 赋值前缀
  - 识别重定向符号（`>`、`>>`）后的路径
  - 识别 heredoc 操作符（`<<`），截断 heredoc 内容
  - 遇到管道/分号停止解析
  - shlex 解析失败时返回哨兵路径 `Path("/⟨unparseable-command⟩")`

### 边界判定

`is_within_boundary(path)` — `Path.resolve()` 后逐个检查是否为某个工作区目录的子路径（`relative_to()`）。

---

## Bypass-immune 安全检查（safety.py）

即使 privileged 模式也不可跳过的硬安全边界。

`is_bypass_immune(tool_name, tool_args) -> (bool, str)`:

### write/edit 工具

检查目标路径是否在受保护列表中：
- Home 目录精确匹配：`.bashrc`、`.zshrc`、`.bash_profile`、`.zprofile`、`.profile`、`.login`、`.gitconfig`
- Home 目录前缀匹配：`.ssh/`、`.gnupg/`
- 项目路径匹配：`.lumi/permissions.json`、`.lumi/permissions.local.json`、`.git/config`

### bash 工具

1. 危险命令模式：`curl ... | sh`、`wget ... | bash`
2. 写入受保护路径检测：匹配重定向（`>`/`>>`）、`tee`、`sed -i`、`cp`、`mv` 的目标位置
   - 同时匹配绝对路径和 `~/` 形式

---

## Bash 安全警告（validators.py）

`validate_bash_command(command) -> list[SafetyWarning]`

非阻断的安全提示，在审批 UI 中展示。基于正则匹配危险模式（force push、hard reset、clean -f、curl pipe、chmod 777 等）。级别分 `warning` 和 `danger`。

---

## 授权路径管理（workspace.py）

维护全局授权目录列表，供 filesystem provider 的 `validate_path()` 使用：

- `set_authorized_directory(path)` — 重置列表为单个主目录
- `add_authorized_directory(path)` — 追加额外目录
- `validate_path(path)` — 检查路径是否在任一授权目录下，不在则抛出 `PermissionError`

`PermissionEngine._rebuild_boundary()` 初始化和配置重载时自动同步。

---

## Graph 节点集成

权限系统在 `lumi/agents/core/nodes.py` 的 `is_use_tool()` 条件路由函数中被调用：

```
is_use_tool() 路由优先级：

1. 无 tool_calls → END
2. 结构化输出 → ExtractStructuredOutput
3. DENY 前置检查 → 命中则 HumanApproval（deny 不可绕过，优先于 bypass）
4. 全部 bypass 类工具 → ToolExecutor（Layer 1: should_bypass_approval）
5. 执行模式策略守卫 → PolicyReject（Layer 2: check_policy）
6. bypass-immune 安全检查 → 命中则 HumanApproval（所有模式）
7. accept_edits 模式: 文件编辑工具(write/edit)工作区内自动放行，其余 → HumanApproval
8. 权限引擎完整评估:
   ├─ 有 DENY → HumanApproval（节点内自动拒绝）
   ├─ privileged 模式: ASK → HumanApproval，其余 → ToolExecutor
   └─ default 模式: 全部 ALLOW + 边界 OK → ToolExecutor，否则 → HumanApproval
9. 引擎不可用: privileged → ToolExecutor，default/accept_edits → HumanApproval
```

关键设计：
- `engine.reload()` 在路由入口调用，实现热重载
- DENY 检查在 bypass 判断之前，确保 deny 规则对 bypass 工具也生效
- bypass-immune 检查在模式策略之后、权限引擎完整评估之前
- 异常时保守处理：评估失败 → 要求人工审批

### HumanApproval 节点

`human_approval()` 构造 `ApprovalRequest` 并通过 `Command(interrupt=...)` 中断 graph 执行，等待 TUI 层的用户响应。二次检查 DENY 规则并自动构造拒绝消息。

---

## 扩展指南

### 添加新的 bypass 工具

在 `capability.py` 的 `_STATIC_EFFECTS` 中声明效果为 `ToolEffect.NONE`、`INTERRUPT` 或 `STATE_MUTATE`：

```python
_STATIC_EFFECTS["my_tool"] = ToolEffect.NONE  # 只读
```

### 添加新的 bash 只读命令

在 `capability.py` 的 `_READONLY_PREFIXES` 中添加命令前缀：

```python
_READONLY_PREFIXES = frozenset({..., "my-readonly-cmd"})
```

### 添加 bypass-immune 受保护路径

在 `safety.py` 的 `_PROTECTED_HOME_PATHS`、`_PROTECTED_HOME_PREFIXES` 或 `_PROTECTED_PROJECT_PATHS` 中添加。

### 添加危险 bash 命令警告

在 `validators.py` 的 `_DANGER_PATTERNS` 中添加 `(re.compile(...), level, message)` 元组。

### 注册自定义执行模式

```python
from lumi.agents.permissions.mode_policy import ModePolicy, register_policy

register_policy("my_mode", ModePolicy(
    name="my_mode",
    label="My custom mode",
    allowed_effects=ToolEffect.NONE | ToolEffect.STATE_MUTATE,
    path_filter=lambda p: p.endswith(".txt"),
))
```

### 添加新的路径操作命令（边界检查）

在 `boundary.py` 的 `_BASH_PATH_COMMANDS` 中添加命令名。

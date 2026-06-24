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
├── matcher.py        # 规则匹配器：命令模式、路径模式
├── boundary.py       # 工作区边界检查器：路径提取与边界判定
├── safety.py         # Bypass-immune 安全检查：受保护文件/命令检测
├── validators.py     # Bash 命令安全警告（非阻断）
├── mode_policy.py    # 执行模式策略（readonly）
└── workspace.py      # 授权路径管理（进程全局兜底 + per-run contextvar 覆盖，供 filesystem provider 使用）

lumi/agents/tools/capability.py  # 只读/写入工具判定 + bash 复合命令拆分
```

---

## 三层工具限制机制

权限系统采用三层架构，各层职责独立：

```
Layer 1: 只读/写入判定 (capability.py)
  ↓ 判断工具调用是否写入操作 → 只读则跳过审批直接执行
Layer 2: 执行模式策略 (mode_policy.py)
  ↓ readonly 模式下拦截不允许的写入操作
Layer 3: 权限引擎 (engine.py)
  ↓ 规则匹配 + 工作区边界 → allow/deny/ask/unmatched
```

### Layer 1: 只读/写入判定（capability.py）

权限系统不再用位标志声明工具副作用，而是用「只读 vs 写入」二分判定：只读工具跳过审批直接执行，写入工具走后续策略与权限引擎。

核心常量与函数：

```python
# 无论参数如何，始终为只读的工具
_ALWAYS_READONLY: frozenset[str] = frozenset({
    "read", "glob", "grep", "skill", "agent",
    "ask", "todos",
})

# 无论参数如何，始终为写入的工具
_ALWAYS_WRITE: frozenset[str] = frozenset({"write", "edit"})

# cron 中的只读操作
_CRON_READONLY_OPS: frozenset[str] = frozenset({"list", "runs"})
```

`is_write_tool(tool_name, tool_args) -> bool`：
- 在 `_ALWAYS_READONLY` 中 → 只读（False）
- 在 `_ALWAYS_WRITE` 中 → 写入（True）
- `bash` → 由 `is_readonly_command(command)` 动态判断
- `cron` → `operation` 不在 `_CRON_READONLY_OPS` 中视为写入
- 未知工具 → fail-closed，默认视为写入

`is_read_only()` 是 `is_write_tool()` 的反义；`is_file_edit_tool()` 仅判断 write/edit（不含 bash）。

`is_readonly_command(command)` 通过白名单 `_READONLY_PREFIXES`（如 `ls`、`cat`、`git status`、`git log`）+ 危险模式检测（重定向 `>`/`>>`、`sed -i`、`curl ... | sh` 等）判断 bash 命令是否只读，未识别命令默认视为非只读（fail-closed）。复合命令拆分后要求每个子命令都匹配只读前缀才算只读。

`split_compound_command(command)` 是字符级状态机，按 `&&`、`||`、`;`、`|`、`&` 拆分复合命令，正确处理单/双引号内的分隔符不拆分。该函数同时供 `capability` 内部和权限引擎的复合命令评估共用。

### Layer 2: ModePolicy（mode_policy.py）

执行模式策略守卫，根据当前模式（`execution_mode` state 字段：`"normal"` / `"readonly"` / 自定义）限制工具调用：

```python
@dataclass(frozen=True)
class ModePolicy:
    name: str                                  # "readonly"
    label: str                                 # 拒绝消息中显示，如 "Readonly mode"
    allow_write: bool = True                   # True → 不限制写入（等同无策略）
    path_filter: Callable[[str], bool] | None = None  # allow_write=False 时的写入路径白名单
```

内置策略：

| 模式 | allow_write | path_filter |
|---|---|---|
| `readonly` | False | None（禁止所有写入） |
| `normal` | 无策略（`get_policy("normal")` 返回 None），走后续权限引擎 | — |

`check_policy(policy, tool_name, tool_args) -> PolicyResult`：
- `allow_write=True` → 全部放行
- 只读操作（`is_write_tool` 为 False）→ 放行
- 写入操作 → 检查 `path_filter`；bash 写入命令 / 文件写入 / 其他写入工具被拒绝，`PolicyResult.reason` 说明原因

`filter_tools_for_mode(tools, policy)` 在子 Agent 创建时静态过滤工具列表：移除写入工具，但 `bash` 保留（运行时动态判断只读性），有 `path_filter` 的策略保留文件写入工具（运行时检查路径）。

扩展方式：`register_policy("my_mode", ModePolicy(...))` 注册自定义模式。

### Layer 3: PermissionEngine（engine.py）

规则匹配 + 工作区边界检查，详见下文。

---

## 核心数据模型（models.py）

所有模型使用 `@dataclass(frozen=True)` 保证不可变：

```python
class Permission(Enum):         # allow | deny | ask
class PermissionDecision(Enum): # allow | deny | ask | unmatched

@dataclass(frozen=True)
class PermissionRule:           # tool: str, permission: Permission
class PermissionConfig:         # workspaces: tuple[str], permissions: tuple[PermissionRule]
class ToolCallInfo:             # name: str, args: dict（批量评估用）
class ApprovalOption:           # key, label, tool_expr（审批 UI 选项）
class ApprovalRequest:          # 传递给 LangGraph interrupt 的审批请求
```

常量：
- `BYPASS_TOOLS`：兼容性保留，新代码应使用 `capability.is_write_tool()`
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

`rebase(project_dir)` 切换项目根目录：重载新目录的权限配置并重建工作区边界。

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

### 边界检查

`check_workspace_boundary(tool_name, tool_args) -> bool`:

1. `WorkspaceBoundary.extract_paths_from_tool_call()` 从工具参数提取路径：标量键 `_PATH_ARG_KEYS`（`file_path` / `path`）取字符串值，列表键 `_PATH_LIST_ARG_KEYS`（`filepaths`，如 `present_files`）逐项提取；新增带路径参数的工具时须把对应键名登记进来，否则不参与边界检查
2. 相对路径基于项目目录解析
3. 逐个检查是否在任一工作区目录下
4. 无法提取路径时视为边界内（不阻断）；解析异常时保守拒绝

`get_boundary_violations()` 返回超出边界的路径列表，供审批 UI 展示。

### 动态规则管理

- `add_allow_rule(tool_expr)` — 持久化到 `permissions.local.json`（审批对话框「始终允许」触发），内存与文件均去重
- `add_workspace(directory)` — 持久化并重建边界检查器
- `add_ephemeral_workspace(directory)` / `remove_ephemeral_workspace(directory)` — 会话级「添加文件夹」，存于引擎独立字段 `_ephemeral_workspaces`（仅内存、不持久化；与会被 `reload()`/`rebase()` 从磁盘覆盖的 `_config.workspaces` 分离，故跨配置重载/项目切换存活）
- `authorized_directories()` — 返回本引擎当前边界（项目根 + 配置 workspaces + 会话级 ephemeral），即每轮 run 注入给 per-run 授权来源的值
- `project_dir` — 本引擎绑定的项目根（会话级，随 `rebase` 变化）
- `add_ephemeral_rules(allow_exprs)` — 仅内存，不持久化（CLI `--allow` 参数）
- `reload()` — 检查文件 mtime 变更后重新加载，重建边界失败时回滚旧配置

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
  "workspaces": ["/extra/dir"],
  "permissions": {
    "allow": ["read", "bash(npm *)"],
    "deny":  ["bash(rm -rf *)"],
    "ask":   ["bash(git push *)"]
  }
}
```

### 合并策略

`_merge_configs(configs)` — 按优先级从低到高遍历，同一工具表达式的规则以最后出现的为准（后覆盖前）。最后追加 `DEFAULT_RULES` 中未被覆盖的规则。workspaces 取并集并去重。

### 持久化

`save_local(config)` — 写入 `local_config_path`（`permissions.local.json`），原子写入（tmpfile + `replace()`），避免写入中途被读取到半成品。

### 热重载

`needs_reload()` — 基于 mtime 检测文件变更（含文件被删除）。`PermissionEngine.reload()` 在每次 `is_use_tool()` 路由时调用，仅在文件变更时实际重新加载。重建边界检查器失败时回滚到旧配置。

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
4. 路径工具（`PATH_TOOLS`：read/write/edit/glob/grep）→ `match_path_pattern()`，路径参数键 `PATH_ARG_KEYS`（`file_path` / `path`）
5. 其他工具（如 MCP 工具）→ 尝试将模式与每个字符串参数值匹配

### 命令模式匹配

`match_command_pattern(pattern, command)`:
- `*` → `.*`（正则），全匹配，DOTALL 支持多行（heredoc）
- 模式以 ` *` 结尾且仅一个通配符 → 尾部空格+参数变为可选（`ls *` 匹配 `ls` 和 `ls -la`）

### 路径模式匹配

`match_path_pattern(pattern, file_path, project_dir)`:
- gitignore 风格：`*` 匹配单层不含 `/`，`**` 匹配零或多层
- `/` 前缀 → 从项目根匹配（fullmatch），否则任意目录层级匹配
- 绝对路径先转为相对于项目根的相对路径

### 表达式构造

供审批 UI 生成「始终允许」选项：
- `build_exact_expr("bash", {"command": "npm install"})` → `"bash(npm install)"`
- `build_pattern_expr("bash", {"command": "npm install"})` → `"bash(npm *)"`
- `build_pattern_expr("edit", {"file_path": "src/main.py"})` → `"edit(**/*.py)"`

---

## 工作区边界检查（boundary.py）

`WorkspaceBoundary` 从工具调用中提取路径并检查边界：

### 路径提取

- 文件工具：从 `file_path` / `path` 标量键、`filepaths` 列表键提取
- bash 工具：解析命令字符串，识别已知路径操作命令（`_BASH_PATH_COMMANDS`：`ls`、`cat`、`cd`、`cp`、`mv`、`rm`、`mkdir`、`touch`、`chmod`、`chown`），提取非标志参数
  - 跳过 `sudo` 和 env 赋值前缀
  - 识别重定向符号（`>`、`>>` 等）后的路径
  - 识别 heredoc 操作符（`<<`），截断 heredoc 内容
  - 遇到管道/分号停止解析
  - shlex 解析失败时返回哨兵路径 `Path("/⟨unparseable-command⟩")`（视为越界）

### 边界判定

`is_within_boundary(path)` — `Path.resolve()` 后逐个检查是否为某个工作区目录的子路径（`relative_to()`）。

---

## Bypass-immune 安全检查（safety.py）

即使 privileged 模式也不可跳过的硬安全边界。仅检查写入类操作（read 不阻断），所有检查都是纯字符串/路径比较，不执行任何命令。

`is_bypass_immune(tool_name, tool_args) -> (bool, str)`:

### write/edit 工具

检查目标路径（`file_path` / `path`）是否在受保护列表中：
- Home 目录精确匹配（`_PROTECTED_HOME_PATHS`）：`.bashrc`、`.bash_profile`、`.zshrc`、`.zprofile`、`.profile`、`.login`、`.gitconfig`
- Home 目录前缀匹配（`_PROTECTED_HOME_PREFIXES`）：`.ssh/`、`.gnupg/`
- 项目路径匹配（`_PROTECTED_PROJECT_PATHS`）：`.lumi/permissions.json`、`.lumi/permissions.local.json`、`.git/config`

### bash 工具

1. 危险命令模式（`_DANGEROUS_COMMAND_PATTERNS`）：`curl ... | sh`、`wget ... | bash`
2. 写入受保护路径检测：通过 `_WRITE_TARGET_TEMPLATES` 匹配重定向（`>`/`>>`）、`tee`、`sed -i`、`cp`、`mv` 的目标位置
   - 同时匹配绝对路径和 `~/` 形式

---

## Bash 安全警告（validators.py）

`validate_bash_command(command) -> list[SafetyWarning]`

非阻断的安全提示，在审批 UI 中展示。基于正则匹配危险模式（`_DANGER_PATTERNS`：force push、`reset --hard`、`clean -f`、curl/wget 管道到 shell、`chmod 777`、写入块设备等）。`SafetyWarning.level` 分 `warning` 和 `danger`。

---

## 授权路径管理（workspace.py）

filesystem provider 的 `validate_path()` 与 bash 工作目录都经此读取授权目录。**两层来源，读取时 per-run 覆盖优先于进程全局兜底**：

- **进程全局兜底** `_authorized_directories`：无 run 上下文时使用（测试、启动期），由 `PermissionEngine._rebuild_boundary()` 在初始化/重载时经 `set_authorized_directory()`（重置为主目录）+ `add_authorized_directory()`（追加）同步。
- **per-run 覆盖** `_run_authorized_source` contextvar：每次 agent run 由 bridge（`_stream` 起点）/ cron（`_invoke_agent` 起点）经 `set_run_authorized_source_for(engine, extra_folders)` 注入本会话引擎的 `authorized_directories` 方法（**实时回调，非快照**）。设置后覆盖兜底。

读取 API（`get_authorized_directory()` / `get_all_authorized_directories()` / `validate_path()`）一律走「run 覆盖 → 全局兜底 → cwd」三级。

**为什么用 per-run contextvar 而非纯进程全局**：一个 `lumi serve` 进程承载多条 WS 连接（每连接一个 bridge / engine），项目随会话绑定（见 [desktop.md](desktop.md)）。若各 engine 都只写同一个进程全局，并发会话会互相清洗——A 会话「添加的目录」会被 B 会话重建边界时抹掉。contextvar 按 run 隔离，各读各的引擎边界；存**实时回调**而非快照，使后台子代理（`asyncio.create_task` 拷贝上下文）与跨工具步 `reload()` 都能即时看到引擎边界的变化。

`set_run_authorized_source_for(engine, ...)` 是 bridge / cron 共用封装：有引擎注入其实时回调；无引擎（构造失败的降级态）降级为 `[cwd, *extra_folders]` 的本轮快照。

> 子代理另经 `shell_session.run_with_shell` 在 `copy_context` 副本里隔离各自的 shell 会话（`cd`/env 不串父/兄弟、用完回收），见 [desktop.md](desktop.md)。

---

## Graph 节点集成

权限系统在 `lumi/agents/core/nodes.py` 的 `is_use_tool()` 条件路由函数中被调用。结构化输出已改为真工具（内部伪工具 `__structured_output__`），不再有独立的 `ExtractStructuredOutput` 节点：

```
is_use_tool() 路由优先级：

1. 无 tool_calls → OnAgentStop（分发 Stop hooks，默认 END）
2. 纯内部伪工具（如结构化输出）→ ToolExecutor（闭包内自校验，绕过权限审批）；
   内部工具与其他工具混合的批次不绕过，落到下方正常评估
3. 权限引擎 DENY 前置检查（所有模式）→ 命中则 HumanApproval（deny 不可绕过，优先于 bypass）
4. 全部只读工具（Layer 1: is_write_tool 全 False）→ ToolExecutor
5. 执行模式策略守卫（Layer 2: check_policy）→ 命中则 PolicyReject
6. bypass-immune 安全检查（所有模式）→ 命中则 HumanApproval
7. accept_edits 模式：文件编辑工具(write/edit)工作区内自动放行，其余 → HumanApproval
8. 权限引擎完整评估:
   ├─ 有 DENY → HumanApproval（节点内自动拒绝）
   ├─ privileged 模式: ASK → HumanApproval，其余 → ToolExecutor
   └─ default 模式: 全部 ALLOW + 边界 OK → ToolExecutor，否则 → HumanApproval
9. 引擎不可用: privileged → ToolExecutor，default/accept_edits → HumanApproval
```

关键设计：
- `engine.reload()` 在路由入口调用，实现热重载
- DENY 检查在 bypass 判断之前，确保 deny 规则对只读/bypass 工具也生效
- bypass-immune 检查在模式策略之后、权限引擎完整评估之前
- 异常时保守处理：评估失败 → 要求人工审批

### HumanApproval 节点

`human_approval()` 先做防御性 DENY 二次检查（命中则跳过 interrupt，构造拒绝 ToolMessage 并 `Command(goto="CallModel")` 让模型调整）。非 DENY 时通过 `interrupt({"type": "tool_approval", ...})` 中断 graph 执行，等待前端（desktop / WS 层）的用户响应。resume 值为 `{"decision": "approve"/"reject"/"cancel", "message": ..., "set_tool_mode": ...}`。权限评估、选项构建、规则持久化由 Bridge / 前端层负责。

---

## 扩展指南

### 添加新的只读工具

在 `capability.py` 的 `_ALWAYS_READONLY` 中加入工具名，即可跳过审批直接执行：

```python
_ALWAYS_READONLY = frozenset({..., "my_readonly_tool"})
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

### 添加新的路径操作命令（边界检查）

在 `boundary.py` 的 `_BASH_PATH_COMMANDS` 中添加命令名。

### 注册自定义执行模式

```python
from lumi.agents.permissions.mode_policy import ModePolicy, register_policy

register_policy(
    "docs_only",
    ModePolicy(
        name="docs_only",
        label="Docs-only mode",
        allow_write=False,
        path_filter=lambda p: p.endswith(".md"),
    ),
)
```

注册后，将该模式名作为 `execution_mode` 传给 `AgentBridge.stream_response(...)`（WS `send_message` 的 `execution_mode` 参数）即可在 `is_use_tool()` 路由中生效：只读操作和写入 `*.md` 的操作放行，其余写入被路由到 `PolicyReject`。

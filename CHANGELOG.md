# Changelog

## [0.1.0a3] - 2026-04-06

### Changed
- `ToolEffect` 五值枚举简化为 `is_write_tool(name, args) -> bool` 二值判定，消除 FILE_WRITE/SHELL_EXEC/STATE_MUTATE/INTERRUPT 的无意义分类
- `ModePolicy.allowed_effects: ToolEffect` 简化为 `allow_write: bool` + `path_filter`
- `cron` 工具按 operation 区分只读（list/runs）与写入（create/update/delete/run/pause），写入操作现在经过权限引擎评估
- `ask`、`todos` 归类为只读工具（原为 INTERRUPT/STATE_MUTATE）
- 文档重组为三层结构：`docs/guides/`（用户指南）、`docs/architecture/`（开发者文档）、`docs/reference/`（外部参考）
- `cache.md` + `cache_docs.md` 合并为 `reference/prompt-caching.md`

## [0.1.0a2] - 2026-04-06

### Changed
- 全局添加 `from __future__ import annotations`，精确化类型标注（`dict` → `dict[str, Any]` 等）
- 将不依赖 `self` 的实例方法提取为模块级函数（checkpoint、registry、loader 等）
- 移除冗余 docstring，保留类型自文档化
- EventRouter `_transition` 由 match/case 重构为 `_PHASE_MAP` 字典查找
- `CommandResult` 改为 `frozen=True, slots=True` 不可变数据类
- 子 Agent 不再继承 `execution_mode`（有意为之，子 Agent 独立运行）

### Fixed
- ExitPlanMode 拒绝时 `tool_cancelled` 标记丢失，导致用户拒绝 plan 后 Agent 继续执行
- 原子写入（job_store、run_log、checkpoint）`except BaseException` 被误改为 `except Exception`，`KeyboardInterrupt` 时临时文件泄漏
- `config_loader` 误删 `Permission.ASK` 配置解析，导致 settings 中 ask 规则被静默忽略
- Scheduler `start()`/`_compensate_missed_runs` 异常捕获过窄（`ValueError, KeyError`），APScheduler 异常导致整个调度器崩溃
- Scheduler `_deliver_and_log`/`_persist_consecutive_errors` 异常捕获过窄（`OSError`），非 IO 异常导致任务执行流中断
- Cron 工具移除 `KeyError` 捕获，job 未找到时返回通用错误而非友好提示
- `_read_text_safe` 文件读取失败时无日志，diff 统计静默不准确

## [0.1.0a] - 2026-04-02

### Changed
- 权限审批流重构：Graph 层 `human_approval` 简化为纯三态契约（approve/reject/cancel），权限评估、选项构建、规则持久化迁移至 Bridge/TUI 层
- `is_use_tool` 路由逻辑统一：bypass-immune → 权限引擎评估 → 模式分派，所有模式共用同一评估循环
- `stream_resume` 不再强制 `tool_mode="auto"`，由 Graph 状态自行维护
- 审批 resume 值从字符串改为 `dict`（`{"decision": ..., "message": ...}`），支持结构化拒绝原因
- `ToolApproval` 简化：单工具直接渲染参数，多工具使用缩进子标题；border_title 显示工具名而非固定文案
- `RuleMatcher` 通配符增强：`"ls *"` 同时匹配 `"ls"`（无参数）和 `"ls -la /dir"`

### Fixed
- **权限评估异常在 privileged 模式下穿透到自动放行**：异常时直接路由到 HumanApproval 而非继续执行
- **`human_approval` DENY 检查的 `except Exception: pass`**：改为记录日志并保守拒绝
- 边界检查异常时向用户展示警告（而非静默忽略）
- `add_allow_rule`/`add_workspace` 引擎不可用时记录 warning
- `_persist_allow_rule` 找不到 tool_expr 时记录 error（而非静默跳过）
- `engine is None` 路径恢复审计日志
- `_render_tool_args` 中 `get_renderer()` 移入 try 块防止注册表异常崩溃 widget

## [0.0.11] - 2026-04-01

### Added
- ASK 权限规则：支持 `ask` 级别配置（优先级介于 deny 和 allow 之间），适用于"允许但需确认"的操作如 `git push`、`npm publish`
- Bypass-immune 安全检查：即使 privileged 模式也不可跳过的保护层，覆盖 shell 配置（`.bashrc`/`.zshrc`）、SSH/GPG 密钥、项目权限配置等敏感路径
- Bash 命令安全警告：审批界面对 `git push --force`、`git reset --hard`、`curl | sh` 等危险模式显示警告辅助决策
- 复合命令拆分评估：bash 复合命令（`&&`、`||`、`;`、`|`）逐个子命令评估权限，取最严格结果
- 临时规则（CLI `--allow`）：支持会话级 allow 规则，不持久化
- 审批组件基类 `BaseApproval`：提取 ToolApproval 和 PlanApproval 的共享逻辑（键盘导航、选项渲染、滚动委派）

### Fixed
- `_check_bash_tool` 补充对 `.ssh/`、`.gnupg/` 前缀路径和项目级受保护路径（`.lumi/permissions.json`、`.git/config`）的写入检查
- `split_compound_command` 从 `shlex.split` 改为字符级状态机，修复引号内分隔符被错误拆分的安全问题
- bypass-immune 安全检查对非字符串参数保守标记为需审批（而非默认放行）
- `Path.home()` 模块级调用改为 try/except，避免 HOME 未设置时导入崩溃
- `human_approval` 中 `engine.evaluate()` 和 `get_boundary_violations()` 添加 try/except 保护
- privileged 模式下 `is_bypass_immune` 调用添加异常保护，防止路由崩溃
- `PermissionEngine.__init__` 的 `except Exception` 收窄为 `(OSError, json.JSONDecodeError, ValueError, KeyError)`
- 移除 `PermissionConfig.permissions is None` 死代码检查
- `_DANGER_PATTERNS` 的 level 字段标注为 `Literal["warning", "danger"]`，消除 type: ignore

### Changed
- 权限评估从两遍扫描（先 deny 后 allow）改为单遍扫描取最严格结果，支持三级优先级：deny > ask > allow
- `PermissionEngine.get_boundary_violations` 添加与 `check_workspace_boundary` 一致的防御性错误处理
- `ToolApproval._render_options` 去重，委派到基类 `BaseApproval._render_options(max_label_len)`

## [0.0.10] - 2026-03-31

### Added
- `LumiAgent.aprune_checkpoints_after()`：支持按 checkpoint_id 清理指定位置之后的 LangGraph checkpoint 数据（SQLite / Postgres / InMemory）
- ToolApproval 审批卡片内容区域支持滚动（`shift+↑↓` / `pgup/pgdn`），解决长内容审批时无法查看完整参数的问题
- PlanApproval 计划审批同样支持内容区域滚动

### Fixed
- Checkpoint 回退（rewind）现在正确恢复到目标轮次执行前的状态：收集目标及之后的变更进行恢复，meta 截断到目标之前
- Rewind 后清理目标之后的所有 LangGraph checkpoint，避免旧分支数据残留
- `_create_checkpoint_before_turn` 检测 stale checkpoint 时区分有 interrupt 和无 interrupt 的情况，仅对无 interrupt 的 stale 状态沿 parent 链回退到 clean checkpoint
- Rewind 回退到第一条消息之前时移除 `checkpoint_id` 并删除整个 thread，等效于空会话
- `_reset_run_state` 中清理 `_pending_system_commands`，防止残留命令影响下一轮

## [0.0.9] - 2026-03-30

### Changed
- Checkpoint 系统从 Shadow Git 重构为文件级快照：不再依赖 git，只追踪 edit/write 工具修改的文件，占用更少磁盘空间
- 新增 `FileChangeTracker`（`lumi/agents/tools/file_tracker.py`）：拦截文件操作记录修改前原始内容
- 新增 `FileCheckpointManager` 替代 `ShadowGitManager`：基于目录结构保存变更清单和原始文件副本
- `FileChangeTracker` 新增 `peek_changes()` 公共方法替代内部属性直接访问
- Checkpoint hash 生成从 `id(object())` + SHA1 改为 `uuid4`，消除碰撞风险

### Fixed
- Checkpoint 三处顶层异常捕获从裸 `except Exception` 收窄为 `(OSError, json.JSONDecodeError, KeyError, ValueError)`
- diff 统计计算中 4 处 `except Exception: pass` 收窄为 `(OSError, UnicodeDecodeError)` 并添加日志
- `_recover_stale_state` 裸 `except Exception: return` 添加 `logger.warning` 日志
- `restore_checkpoint` 部分文件恢复失败时正确返回 `False` 而非 `True`
- `_load_meta` 备份失败时尝试删除损坏文件而非静默忽略
- `_load_changes` 备份文件缺失时添加警告日志
- `shutil.rmtree(ignore_errors=True)` 改为显式 `try/except OSError` 加日志
- `record_pre_edit` 异常捕获收窄为 `(OSError, UnicodeDecodeError)`
- Shell 会话关闭时显式清理子进程 transport，修复 pytest 中 `RuntimeError('Event loop is closed')` 警告
- 用户提示中 "Shadow Git 未初始化" 更新为 "Checkpoint 未初始化"
- 移除 `_unsafe_filename` 死代码、冗余 `asyncio` 导入、无用注释
- 消除 `_compute_diff_stat` 与 `_compute_diff_stat_live` 的 ~30 行重复代码
- `create_checkpoint` 中双重 `_load_meta()` 调用优化为单次

## [0.0.8] - 2026-03-30

### Added
- Workspace 隔离：cron 定时任务和会话历史按工作目录隔离存储，不同项目互不干扰
- Cron 错过任务补偿：Scheduler 启动时自动检测并补偿执行离线期间错过的定时任务
- Cron TUI 执行状态指示器：InputBar 右侧显示正在执行的定时任务动画
- `lumi/utils/workspace_id.py`：基于 SHA256 的工作目录唯一标识生成
- `lumi/tui/text_cleaning.py`：统一的 XML 标签过滤和用户输入还原模块
- `RunLog.get_last_run_sync()`：同步获取最近执行记录，用于启动时补偿检查

### Fixed
- Checkpoint 截断 bug：`restore_to` 现在正确保留目标 checkpoint 记录而非截断它
- AgentBridge 错误信息增强：流式错误包含异常类型和 cause 链，图状态异常提供详细诊断
- MCP stdio 子进程 stderr 静默：避免 MCP 子进程日志输出污染 TUI 界面
- Headless 模式移除 stderr 重定向 hack，改为 MCP 层面解决
- Agent 响应多模态 content 提取：正确处理 list 类型的 content 块

### Changed
- TUI 审批对话框（AskDialog、ToolApproval、PlanApproval）从 ToolBlock 内部挂载改为 InputBar 前挂载
- 只读工具（read、glob、grep）不再检查工作区边界，仅写操作受边界保护
- AgentGroup `add_agent` 改为 async，直接 await mount 替代 `call_after_refresh`
- Agent 工具 schema 延迟初始化：避免模块导入时重复加载配置
- LLM 超时从 120s 增大到 300s
- `structured_output` 和 `chat_chain` 新增 httpx 网络错误重试（RemoteProtocolError、ConnectError、ReadError）
- 权限检查日志改为始终输出（不仅在需要审批时），便于排查
- TodosBar 操作统一使用 `_query_safe` 替代 try/except NoMatches
- AgentGroup 统一 `_get_entry` 方法减少重复代码，统计摘要在无数据时省略括号
- `_prepend_plan_reminder` 泛化为 `_prepend_text_block`，消除重复的 content 注入逻辑
- `docs/cron.md`、`docs/permissions.md` 文档更新

## [0.0.7] - 2026-03-28

### Added
- 新增 Style 系统：支持通过 `style` 配置切换系统提示词风格，内置 `default` 和 `code` 两种风格
- 新增 `lumi/styles/` 目录，包含风格内置的 prompts、tools、agents 配置
- 新增 CLI `--style / -s` 参数，运行时覆盖 config.yaml 中的风格配置
- 新增 CLI `--privileged-danger` 参数，启动时进入特权模式跳过所有审批
- 新增 `docs/styles.md` 文档
- Plan Mode 支持用户手动开启（Shift+Tab 切换 `⏸ plan` 指示器）

### Changed
- 移除 `approve` 工具模式，简化为 `auto` / `plan` / `privileged` 三种状态指示
- Plan Mode 工具提示词从硬编码迁移到 style MD 文件加载，缺失时抛出 RuntimeError 而非静默回退
- 系统提示词加载逻辑重构：先从 style 内置目录读取，再用用户 `.lumi/prompts/` 覆盖
- `BridgeEvent` 移除 `approval_mode` 字段
- `InputBar` 重构：Shift+Tab 改为切换 plan mode，移除 tool_mode 循环切换
- `PlanApproval` 组件布局优化，计划文件名突出显示
- `docs/config.md`、`docs/permissions.md`、`docs/plan.md` 文档更新

## [0.0.6] - 2026-03-28

### Added
- 新增 Plan Mode（计划模式）：Agent 可在执行非平凡任务前进入只读规划阶段，设计方案后提交用户审批
- 新增 `EnterPlanMode` / `ExitPlanMode` 工具，支持从 `.lumi/prompts/tools/EnterPlanMode.md` 自定义提示词
- 新增 `PlanApproval` TUI 审批组件，展示计划文件内容并提供批准/拒绝操作
- 新增 MIT LICENSE
- 新增 `docs/plan.md` 文档

### Changed
- `ToolApproval` 组件重构为圆角卡片布局（`╭│├╰`），标题嵌入顶部边框，提示嵌入底部边框
- `CLAUDE.md` 全面重写，补充架构概要、工具系统、权限系统、TUI 架构、子 Agent 等详细说明
- `README.md` 增强：新增徽章、Headless/浏览器模式说明、文档索引表，精简冗余内容
- `pyproject.toml` description 更新为中文描述

### Fixed
- 修复 `test_preprocess_skill_injection` 中因系统信息注入导致的测试不稳定
- 修复 `test_skill_injector` 中 system-reminder 格式断言与实际输出不匹配的问题
- 修复 `test_filesystem` 中空文件警告文本与实际返回值不一致

## [0.0.5] - 2026-03-25

### Added
- 工具结果卸载功能：大文件自动写入 `~/.lumi/offload/` 目录，消息中保留文件路径引用，避免占用过多上下文窗口
- 新增 `lumi.cli` CLI 入口模块，支持 `textual-serve` 集成

### Changed
- Token 配置从固定 token 数改为相对于 `context_length` 的比例配置（`once_tool_ratio`、`trim_messages_ratio`），更灵活适配不同模型的上下文窗口
- TUI 渲染架构重构：统一为 WidgetAssembler 模式，支持摘要层和懒渲染优化
- ToolGroup 和 AgentGroup 支持合并显示与轻量摘要模式，提升长对话可读性
- TUI 事件路由解耦，采用渲染器注册机制，提高可扩展性

### Fixed
- 修复 sub-agent 审批 replay 渲染与 todos-bar 持久化问题
- 修复 TUI ask 取消状态处理、ToolBlock 焦点样式与 resume 提示
- 修复 TUI 滚动异常、消息恢复分组逻辑
- 增强权限引擎异常处理与边界检查

## [0.0.3] - 2026-03-19

### Added
- 新增 Checkpoint 回退机制：自动快照工作区文件和 LangGraph 会话状态，支持一键回退到任意历史节点
- 新增 `/rewind` 命令和双击 Esc 快捷方式打开 Rewind 界面，选择并回退到历史 checkpoint
- 新增 `ShadowGitManager`：在项目目录外维护独立 git 仓库，追踪文件变更，不影响项目本身的 Git 历史
- 新增 `lumi/agents/tools/checkpoint.py` 模块，实现 checkpoint 创建、列表、恢复和 diff 统计
- 新增 `lumi/tui/screens/rewind_screen.py` 组件，提供 checkpoint 选择界面
- 新增 `docs/checkpoint.md` 文档，详细说明 Checkpoint 功能的工作原理和使用方式

### Changed
- `ListScreen` 支持配置初始选中项索引，适配 Rewind 界面自动选中最新 checkpoint 的需求
- `AgentBridge` 集成 Shadow Git 管理，在每轮对话前自动创建 checkpoint 并关联 LangGraph checkpoint_id
- `LumiApp._restore_messages` 支持指定 checkpoint_id 参数，用于回退后重建历史消息

## [0.0.2] - 2026-03-17

### Changed
- `recursion_limit` 默认值从 100 调整为 5000，适配复杂任务场景
- `apply_env` 环境变量注入策略改为始终覆盖系统环境变量
- scheduler、API、TUI 三处 agent 调用统一传入 `recursion_limit` 配置

### Removed
- 移除 `max_upload_size_mb` 配置字段

## [0.0.1] - 2026-03-17

首个正式发布版本。

# Changelog

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

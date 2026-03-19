# Changelog

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

# Changelog

## [0.0.2] - 2026-03-17

### Changed
- `recursion_limit` 默认值从 100 调整为 5000，适配复杂任务场景
- `apply_env` 环境变量注入策略改为始终覆盖系统环境变量
- scheduler、API、TUI 三处 agent 调用统一传入 `recursion_limit` 配置

### Removed
- 移除 `max_upload_size_mb` 配置字段

## [0.0.1] - 2026-03-17

首个正式发布版本。

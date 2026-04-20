"""Agent 运行时状态 — 跨子系统共享的有状态对象。

此包收集被 ``core/``、``tools/``、``tui/``、``api/`` 等多处消费的运行时状态：

- ``session``: 持久化 bash session 管理
- ``checkpoint``: 文件级快照与回退
- ``file_tracker``: 文件修改追踪
- ``bg_tasks``: 后台任务元数据注册表

外部使用统一走全路径（如 ``from lumi.agents.runtime.session import ...``），
此 ``__init__`` 不做 re-export，避免双入口歧义。
"""

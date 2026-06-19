"""Agent 运行时状态 — 跨子系统共享的有状态对象。

此包收集被 ``core/``、``tools/``、``gateway/`` 等多处消费的运行时状态：

- ``shell_session``: 持久化 bash shell 会话管理
- ``bg_process``: 后台 Bash 进程生命周期管理
- ``checkpoint``: 文件级快照与回退
- ``file_tracker``: 文件修改追踪
- ``bg_tasks``: 后台任务元数据注册表

外部使用统一走全路径（如 ``from lumi.agents.runtime.shell_session import ...``），
此 ``__init__`` 不做 re-export，避免双入口歧义。
"""

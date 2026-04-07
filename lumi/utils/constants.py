"""内部应用常量

集中管理行为性常量（超时、限制、间隔、重试）。
用户可配置的设置请参见 lumi/utils/config/global_models.py (GlobalConfig)。
"""

from typing import Final

# ── 通知 ──

NOTIFICATION_POLL_INTERVAL: Final[float] = 2.0
"""后台任务通知轮询间隔（秒）"""

# ── Stream / 重试 ──

MAX_STREAM_RETRIES: Final[int] = 2
"""流式传输网络错误最大重试次数"""

RETRY_BASE_WAIT: Final[int] = 5
"""重试基础等待时间（秒）"""

# ── Shell 会话超时 ──

DEFAULT_COMMAND_TIMEOUT: Final[float] = 120.0
"""execute() 默认超时秒数"""

CWD_QUERY_TIMEOUT: Final[float] = 5.0
"""get_cwd() 查询超时秒数"""

GRACEFUL_SHUTDOWN_TIMEOUT: Final[float] = 5.0
"""进程优雅关闭等待秒数"""

# ── 图片处理 ──

MAX_IMAGE_SIZE: Final[int] = 20 * 1024 * 1024  # 20MB
"""图片下载最大字节数"""

IMAGE_FETCH_TIMEOUT: Final[float] = 30.0
"""图片下载超时秒数"""

IMAGE_TOKEN_ESTIMATE: Final[int] = 800
"""单张图片的 token 固定估算值"""

# ── Cron 调度 ──

MAX_RUN_LOG_FILE_SIZE: Final[int] = 2 * 1024 * 1024  # 2MB
"""单个 JSONL 运行日志文件最大字节数"""

MAX_CRON_RETRIES: Final[int] = 3
"""Cron 任务最大重试次数"""

CRON_BACKOFF_INTERVALS: Final[tuple[int, ...]] = (30, 60, 300)
"""Cron 重试退避间隔（秒）"""

MAX_NOTIFICATIONS: Final[int] = 100
"""通知历史最大记录数"""

# ── TUI 显示限制 ──

MAX_LIVE_WIDGETS: Final[int] = 80
"""ChatLog DOM 中保留的最大 widget 数量"""

KEEP_WIDGETS: Final[int] = 50
"""DOM 压缩后保留的 widget 数量"""

MAX_RESTORE_MESSAGES: Final[int] = 60
"""恢复历史消息的最大数量"""

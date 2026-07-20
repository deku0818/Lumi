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

BASH_MAX_OUTPUT_BYTES: Final[int] = 30 * 1024
"""bash 前台 execute() 单次 stdout 累积上限（字节）。超限后续输出丢弃，附 trailer 告知。"""

# ── 消息注入标记 ──

ATTACHED_FILE_TAG: Final[str] = "attached-file"
"""文件附件路径在消息中的包裹标签 <attached-file>路径</attached-file>。

纯模型侧约定（agent 用 read 读取路径）：bridge.stream_response 把附件路径拼成
标签块注入 content；显示侧不解析——附件胶囊数据走 lumi.items 的 files 字段。"""

FEISHU_THREAD_PREFIX: Final[str] = "feishu-"
"""飞书渠道会话的 thread 前缀（确定性派生 feishu-{key}，key 见 inbound.session_key_of）。

单一事实源：inbound.feishu_thread_id 的派生与 gateway.session._channel_of 的
判定（会话列表标注 / 只读守卫 / 通知轮跳过）共用此常量。"""

LUMI_META_KEY: Final[str] = "lumi"
"""HumanMessage.additional_kwargs 里 Lumi 渲染元数据的命名空间键。

写入（bridge.stream_response：消息级 ts + IM 渠道的 items）与读取
（gateway.session._user_items）共用，防写读两端字段名漂移。"""

SENDER_TAG: Final[str] = "sender"
"""IM 渠道消息的发送者标注 <sender>姓名</sender>\\n正文。

渠道无关约定（飞书首用）：纯给模型看（群聊里分清谁说的）。显示侧不解析
此标签——每条原始消息的 {sender, ts, text} 结构化存于
HumanMessage.additional_kwargs["lumi"]["items"]（见 _user_items），两条链路分离。"""

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

MAX_CRON_RUN_THREADS: Final[int] = 50
"""每个 cron 任务保留会话 checkpoint 的最近执行次数，超出部分清理"""

MAX_NOTIFICATIONS: Final[int] = 100
"""通知历史最大记录数"""

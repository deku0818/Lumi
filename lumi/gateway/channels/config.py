"""对外 IM channel 的配置模型。

不放在 ``lumi/utils/config/models.py``（config.json 的 schema）——channel 配置由 UI 经
WS RPC 管理、持久化到 ``lumi.json`` 的 "channels" 分区（见 ``channels/store.py``），与 config.json
解耦。模型仍用 pydantic，供 store 校验与 channel 构造共用。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FeishuChannelConfig(BaseModel):
    """飞书 / Lark Channel 配置（lark-oapi WebSocket 长连接，无需公网 webhook）。

    凭证支持 ``${ENV_VAR}`` 语法引用环境变量，channel 启动时经 ``os.path.expandvars``
    解析，避免明文。
    """

    enabled: bool = Field(default=False, description="是否启用飞书 Channel")
    app_id: str = Field(default="", description="飞书应用 App ID（支持 ${ENV} 引用）")
    app_secret: str = Field(
        default="", description="飞书应用 App Secret（支持 ${ENV} 引用）"
    )
    allow_from: list[str] = Field(
        default_factory=lambda: ["*"],
        description='白名单 open_id 列表：["*"] 全部允许（默认）；[] 全部拒绝；其余仅列表内',
    )
    group_policy: Literal["mention", "open"] = Field(
        default="mention",
        description="群聊策略：mention=仅 @机器人 时响应（默认）；open=响应所有群消息",
    )
    tool_mode: Literal["auto", "privileged"] = Field(
        default="auto",
        description="工具审批模式：auto=AI 审批（默认）；privileged=自动放行。两种模式下"
        "泄漏出来的人工审批一律自动拒绝（飞书只保留 ask 询问卡片）",
    )
    workspace: str = Field(
        default="", description="飞书会话绑定的项目根目录；空则用进程 cwd"
    )
    daily_dream_enabled: bool = Field(
        default=False,
        description="每日定时记忆整理：到点对有新消息的会话先串行 dream（沉淀记忆）、"
        "再并发 summary（压缩历史），让常驻会话不无限膨胀",
    )
    daily_dream_time: str = Field(
        default="03:00",
        description='每日整理时间，本地时区 "HH:MM"（建议低峰时段）',
    )
    summary_max_concurrency: int = Field(
        default=3,
        ge=1,
        le=8,
        description="summary 阶段最大并发数（限流防接口 429）；dream 恒串行不受此值影响",
    )

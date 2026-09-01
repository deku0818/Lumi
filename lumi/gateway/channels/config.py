"""对外 IM channel 的配置模型。

不放在 ``lumi/utils/config/models.py``（config.json 的 schema）——channel 配置由 UI 经
WS RPC 管理、持久化到 ``lumi.json`` 的 "channels" 分区（见 ``channels/store.py``），与 config.json
解耦。模型仍用 pydantic，供 store 校验与 channel 构造共用。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from lumi.utils.constants import FEISHU_THREAD_PREFIX


class ChannelRuntimeConfig(BaseModel):
    """IM 渠道共享的「会话怎么跑」运行时配置。

    所有渠道的 Agent 都要回答同一组问题——怎么审批、在哪个项目跑。抽成基类供各渠道
    config 继承（字段结构复用，值各渠道各存一份，不共享），新渠道（企微等）接入时直接
    得到这组能力，无需重写。

    模型与思考档位刻意**不在**这里：它们是会话属性，由会话内的 ``/model`` / ``/effort``
    定、按 thread 存（见 ``sessions/session_model.py``），未设则跟随新会话默认。渠道级
    固定模型会让同一渠道下的每个群被迫同档，且与会话级设定构成两套优先级。
    """

    tool_mode: Literal["auto", "privileged"] = Field(
        default="auto",
        description="工具审批模式：auto=AI 审批（默认）；privileged=自动放行。两种模式下"
        "泄漏出来的人工审批一律自动拒绝（飞书只保留 ask 询问卡片）",
    )
    workspace: str = Field(
        default="",
        description="渠道会话绑定的项目根目录。必填、无兜底——空则渠道拒绝启动"
        "（不退回 serve 进程 cwd），默认空只是「尚未配置」的初值",
    )


class FeishuChannelConfig(ChannelRuntimeConfig):
    """一个飞书 / Lark 机器人的配置（lark-oapi WebSocket 长连接，无需公网 webhook）。

    一台机器可配多个机器人，每个绑定一个项目（1:1，见 ``store.save_feishu_bot`` 的
    唯一性校验）。凭证支持 ``${ENV_VAR}`` 语法引用环境变量，channel 启动时经
    ``os.path.expandvars`` 解析，避免明文。运行时字段（tool_mode/workspace）继承自
    ``ChannelRuntimeConfig``。
    """

    id: str = Field(
        default="",
        description="机器人稳定标识（8 hex，store 保存时生成）：manager 槽位、"
        "会话 thread 命名空间、lark-cli profile 名都由它派生",
    )
    name: str = Field(default="飞书机器人", description="展示名（仅本机 UI 用）")
    legacy_threads: bool = Field(
        default=False,
        description="旧版单机器人迁移标记：会话 thread 沿用不带机器人段的 "
        "feishu-{key}（保住历史会话），新建机器人恒为 False 走 feishu-{id}-{key}",
    )
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
    minutes_enabled: bool = Field(
        default=False,
        description="妙记纪要：录音 / 会议生成妙记后自动取逐字稿、整理纪要并推送私聊。"
        "依赖 lark-cli 已安装并完成用户授权（读妙记必须 user 身份）",
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

    @property
    def thread_prefix(self) -> str:
        """本机器人会话 thread 的确定性前缀。

        同一个群里可能坐着两个 Lumi 机器人（不同项目各一个），chat_id 相同——thread
        必须带机器人段才不撞会话。仅旧版迁移来的那一条沿用裸 ``feishu-``，保住历史。
        前端 ``desktop/src/lib/utils.ts`` 的 ``botOfThread`` 按同一规则反解会话归属：
        thread id 是持久化的 checkpoint 标识，此派生等于 wire 约定，两端一起才改。
        """
        if self.legacy_threads:
            return FEISHU_THREAD_PREFIX
        return f"{FEISHU_THREAD_PREFIX}{self.id}-"

    cli_profile: str = Field(
        default="",
        description="已同步的 lark-cli profile 名（会话 env 注入 LARKSUITE_CLI_PROFILE 用）。"
        "由 lark_profile.sync_profile 解析后写回：机器上已有指向本 app 的 profile 就复用"
        "（lark-cli 强制 app_id 跨 profile 唯一，且复用能带上既有用户授权），没有才自建"
        " lumi-{id}。空 = 尚未同步，会话不注入（回落全局 active profile）",
    )

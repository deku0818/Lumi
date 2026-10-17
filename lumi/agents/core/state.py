from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, NotRequired, TypedDict

if TYPE_CHECKING:
    from lumi.agents.core.broker import ApprovalBroker
    from lumi.agents.core.inbox import Inbox
    from lumi.agents.permissions.engine import PermissionEngine

from dataclasses import dataclass, field
from pathlib import Path

from langgraph.channels.delta import DeltaChannel
from langgraph.graph.message import _messages_delta_reducer


@dataclass
class LumiAgentContext:
    tools: list = field(default_factory=list)
    project_dir: Path | None = field(default=None)
    """本 agent 所属项目根（None = 无项目/全局）。子代理、workflow 建下级 agent 时
    据此显式传递父级项目（MCP 分层加载 + 冷池等待），不依赖 contextvar。"""
    system_prompt: str = field(default="")
    model_name: str = field(default="")
    """模型名；连接（base_url / api_key）由 create_llm 按供应商 profile 解析。"""
    provider: str = field(default="")
    """model_name 所属的供应商 profile id；(连接, 模型) 才是完整身份——同名模型
    存在于多个 profile 时，仅按名反查会把连接/档位/限额取到别家（active 优先）。
    空 = 未知来源（老渠道配置 / headless），resolve 退回按名反查，行为同旧版。"""
    effort: str | None = field(default=None)
    """思考档位覆盖：None = 跟随该模型 profile 的档位（desktop 会话走这条，由 ModelPicker
    存进 provider_store）；非 None = 强制用此档位、绕过 profile（IM 渠道会话用它独立配置
    思考模式而不改全局）。auto 表示不注入思考参数，ultra 为 Lumi 顶档。"""
    permission_engine: PermissionEngine | None = field(default=None)
    """PermissionEngine 实例，用于工具权限评估"""
    tool_mode: str = field(default="default")
    """工具审批模式（运行时真相源，所有节点共享同一 context 实例，bridge 侧可随时改）:
    所有模式下 deny 拒绝、allow 放行，ask / 未匹配的调用分别按模式处理：
    - "default": 经审批 Broker 询问用户
    - "accept_edits": 未匹配规则的 write/edit 自动放行，其余询问用户
    - "privileged": 自动放行
    - "auto": 每个调用独立交 AI 分类器裁决 approve / reject
    放在 context（而非 state）：state 是每个 super-step 的快照，运行中改不动；context
    是共享可变引用，bridge 改它后下一个节点 runtime.context 立即读到 → 支持运行中实时切换。"""
    mode_parent: LumiAgentContext | None = field(default=None)
    """前台子代理指向父 context：审批模式是会话属性，读写都落到根 context（见
    :meth:`mode_root`）——父会话运行中切换模式对子代理即时生效，子代理审批卡里切换也
    回到会话本身。后台 / workflow 子代理无审批通道、固定 auto，不设。"""
    approval_broker: ApprovalBroker | None = field(default=None)
    """在途审批 Broker，由 bridge 在 create_agent 后注入（与 permission_engine 同源）。
    节点 / ask 工具经它原地 await 审批。子 agent 由 agent 工具从父 context 传播。
    无 bridge 的纯 graph 调用（headless）保持 None。"""
    get_goal: Callable[[str], str] | None = field(default=None)
    """按 thread_id 读会话当前 ``/goal`` 条件（未设定返回空串），由 bridge 注入。
    goal 存 sessions 层的 sidecar，core 不直接依赖 sessions——同 approval_broker 的
    注入方式。None（headless / cron / 子 agent）= 无目标驱动。"""
    clear_goal: Callable[[str], None] | None = field(default=None)
    """按 thread_id 清会话 goal（达成 / 永远达不成时由 goal_stop_hook 调用），同上注入。"""
    inbox: Inbox | None = field(default=None)
    """运行中插话收件箱，由 bridge 注入。图在工具边界 / 模型想结束时取走注入当前轮。
    None（headless / cron / 子 agent）= 不接受插话。"""
    env_extra: str = field(default="")
    """<env> 块尾部追加的会话级条目行（渠道无关，已按块内格式渲染好、含缩进）。

    IM 渠道写"会话来源: 飞书 + 场景/群名/chat_id 子项"，让模型知道自己在哪个群、
    拿得到发消息要用的 id。层级长什么样由渠道自己定（core 不认识群聊私聊），故这里
    收的是文本而非结构。desktop 会话恒空 → <env> 块与改动前逐字节相同。子 agent 由
    agent 工具从父 context 传播（env 块本就注入子 agent，缺了会出现"父知道在哪个群、
    子不知道"的割裂）。"""
    memory_enabled: bool = field(default=False)
    """是否为本 agent 注入持久记忆（MEMORY.md 索引 + 系统提示词行为说明）。
    默认 False（opt-in），与 create_agent 一致；仅 bridge 的主对话 agent 置 True。
    AGENTS.md 的按需读取规则不受此开关影响，主/子 agent 均带此规则。"""

    def mode_root(self) -> LumiAgentContext:
        """审批模式的归属 context：沿 ``mode_parent`` 上溯到会话根。"""
        ctx = self
        while ctx.mode_parent is not None:
            ctx = ctx.mode_parent
        return ctx


class LumiAgentState(TypedDict):
    messages: Annotated[
        list, DeltaChannel(_messages_delta_reducer, snapshot_frequency=100)
    ]
    """对话历史。通道用 ``DeltaChannel``（增量 checkpoint）而非 ``add_messages``：
    后者每个 super-step 都把整条历史重新序列化落库，长会话下 checkpoint 库按轮数
    平方增长。``DeltaChannel`` 只存哨兵 + 回放祖先写入，每 ``snapshot_frequency``
    次更新写一次完整快照。

    ``snapshot_frequency`` 必须显式给：官方默认 1000 实际等于「几乎不快照」，而恢复
    状态要一路回放到上一个快照，于是**读**退化成 O(链长)——读一次 state 在生产里
    每轮至少三次（``_recover_stale_state`` / 跑图 / ``_turn_complete_event``），会话
    列表还会并发 25 个。200 轮长回复会话实测（单次 ``aget_state`` / 库大小）：

    - ``add_messages``：2.4 ms / 497 MB
    - ``snapshot_frequency=1000``（默认）：11.2 ms / 4.1 MB —— 且随轮数持续增长
    - ``snapshot_frequency=100``：2.7 ms / 6.8 MB —— 回放深度有上限，读不再随会话变老

    取 100：读与 ``add_messages`` 持平，库仍小 70 倍以上。

    ``_messages_delta_reducer`` 是官方配套的批量 reducer（带 ``_`` 前缀、标
    Experimental，但它是唯一与 ``add_messages`` 语义对齐的批量实现）。与
    ``add_messages`` **一致**的部分锁在 ``tests/test_delta_channel.py``：同 id 替换
    （``persist_partial_reply`` 的承重墙）、``RemoveMessage`` 删除、``Overwrite``
    整体替换、以及对存量 ``add_messages`` checkpoint 的读兼容。

    LangGraph 1.2.15 在节点与 ``aupdate_state`` 写入时均补齐缺失的消息 id，含
    ``Overwrite`` 内的消息；读回保持同一 id。离线 ``Overwrite`` 也会生成完整快照，
    后续恢复不再回溯压缩前的增量链。"""
    todos: NotRequired[list]
    """旧 checkpoint 的兼容字段；内置 todo 工具与前端展示已移除。"""
    output_schema: NotRequired[dict[str, Any]]
    """结构化输出的 JSON Schema"""
    structured_output: NotRequired[dict[str, Any]]
    """结构化输出结果"""
    tool_cancelled: NotRequired[bool]
    """本轮被终止（用户取消 ask、hook Block、结构化输出失败上限）时置 True，供条件边
    路由到 END；下一轮 PreprocessMessages 开头复位。字段名沿用旧名，免迁移存量 checkpoint"""
    ptl_retry: NotRequired[bool]
    """CallModel 撞 prompt-too-long 后置 True 并路由回 Compact 强制压缩，
    成功响应后清 False。置位期间再撞 PTL 直接抛原错误——每次 PTL 只换一次压缩机会。"""
    depth: NotRequired[int]
    """子 agent 委派深度：主 agent 为 0，每委派一层 +1。
    agent 工具据此限制最大委派层数（见 agents.max_delegation_depth）。"""

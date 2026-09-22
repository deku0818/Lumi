from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, NotRequired, TypedDict

if TYPE_CHECKING:
    from lumi.agents.core.broker import ApprovalBroker
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
    - "default": 权限引擎评估，未通过则经审批 Broker 询问用户
    - "accept_edits": 文件编辑工具(write/edit)在工作区内自动放行，bash 等仍需审批
    - "privileged": 权限引擎评估但自动放行，仅 bypass-immune 仍需审批
    - "auto": AI 审批模式——本该问人的批次交分类器(AutoClassify 节点)裁决
      approve/ask/reject；DENY 与 bypass-immune 仍免疫，强制走人工审批
    放在 context（而非 state）：state 是每个 super-step 的快照，运行中改不动；context
    是共享可变引用，bridge 改它后下一个节点 runtime.context 立即读到 → 支持运行中实时切换。"""
    approval_broker: ApprovalBroker | None = field(default=None)
    """在途审批 Broker，由 bridge 在 create_agent 后注入（与 permission_engine 同源）。
    节点 / ask 工具经它原地 await 审批。子 agent 由 agent 工具从父 context 传播。
    无 bridge 的纯 graph 调用（headless）保持 None。"""
    widen_boundary: Callable[[list[str]], None] | None = field(default=None)
    """放宽本会话工作区边界的回调，由 bridge 注入（同 approval_broker，事后赋值）。
    授权（人工审批 / auto 分类器 / privileged）通过后调用，把越界路径所在目录纳入本
    会话工作区。无 bridge 的纯 graph 调用（headless）保持 None，边界不放宽。"""
    get_goal: Callable[[str], str] | None = field(default=None)
    """按 thread_id 读会话当前 ``/goal`` 条件（未设定返回空串），由 bridge 注入。
    goal 存 sessions 层的 sidecar，core 不直接依赖 sessions——同 widen_boundary 的
    注入方式。None（headless / cron / 子 agent）= 无目标驱动。"""
    clear_goal: Callable[[str], None] | None = field(default=None)
    """按 thread_id 清会话 goal（达成 / 永远达不成时由 goal_stop_hook 调用），同上注入。"""
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
    项目说明 LUMI.md 不受此开关影响，主/子 agent 均注入。"""


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

    **唯一不一致、且会咬人的地方**：``add_messages`` 给 ``id=None`` 的消息自动补
    UUID，本通道**只在节点写入时**补（LangGraph 的 ``put_writes``），``aupdate_state``
    不补。所有离线写回必须先过 ``node_helpers.messages.stamp_missing_ids``，否则那些
    消息一辈子没有 id，而 rewind 截断 / 半截判重 / 压缩选材全按 id 认消息。这条差异
    正反两面都有锁定用例。"""
    todos: NotRequired[list]
    """任务列表，用于追踪复杂任务的执行进度"""
    output_schema: NotRequired[dict[str, Any]]
    """结构化输出的 JSON Schema"""
    structured_output: NotRequired[dict[str, Any]]
    """结构化输出结果"""
    tool_cancelled: NotRequired[bool]
    """工具执行被用户取消时置 True，供条件边路由到 END"""
    ptl_retry: NotRequired[bool]
    """CallModel 撞 prompt-too-long 后置 True 并路由回 Summarizer 强制压缩，
    成功响应后清 False。置位期间再撞 PTL 直接抛原错误——每次 PTL 只换一次压缩机会。"""
    depth: NotRequired[int]
    """子 agent 委派深度：主 agent 为 0，每委派一层 +1。
    agent 工具据此限制最大委派层数（见 agents.max_delegation_depth）。"""

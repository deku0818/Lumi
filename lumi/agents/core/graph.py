import os
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import TracePolicy
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from lumi.agents.core.nodes import (
    after_tool_executor,
    auto_classify,
    call_model,
    human_approval,
    is_use_tool,
    on_agent_stop,
    on_call_model_error,
    preprocess_messages,
    summarizer,
    tool_executor,
)
from lumi.agents.core.state import LumiAgentContext, LumiAgentState
from lumi.agents.permissions.engine import PermissionEngine
from lumi.agents.tools import get_tools
from lumi.agents.tools.providers.todo import Todo
from lumi.models import provider_store
from lumi.utils.config import CheckpointMode, GlobalConfigManager, get_config
from lumi.utils.logger import logger


def _digest_history(value):
    """节点 trace 输入里把整段消息历史换成一行摘要。

    每个节点的 ``on_chain_start`` 默认携带**全量 state**（含全部消息）。bridge 一条
    都不消费（只认 ``on_chat_model_*`` / ``on_tool_*`` / ``on_custom_event``），却要
    让整段历史随每个 super-step 的每个节点在事件流里过一遍；接了 LangSmith 的话还会
    原样上传。这里只影响**记录**的内容，传给节点的值不变（见 TracePolicy 文档）。
    """
    if not isinstance(value, dict) or "messages" not in value:
        return value
    return {**value, "messages": f"<{len(value['messages'])} 条历史，trace 已省略>"}


_DIGEST_HISTORY = TracePolicy(process_inputs=_digest_history)


class LumiAgent:
    def __init__(self, checkpointer: BaseCheckpointSaver | None = None):
        """
        初始化 LumiAgent

        Args:
            checkpointer: checkpointer 实例，用于状态持久化，默认为 None（不使用）
        """
        self.checkpointer = checkpointer
        self.builder = StateGraph(LumiAgentState)
        self._draw_nodes()
        self._draw_edges()
        self.graph = self.builder.compile(checkpointer=checkpointer)

    def _draw_nodes(self):
        """添加节点"""

        def add(name: str, action, **kwargs) -> None:
            self.builder.add_node(name, action, trace_policy=_DIGEST_HISTORY, **kwargs)

        add("PreprocessMessages", preprocess_messages)
        add("Summarizer", summarizer)
        # PTL 兜底走节点级 error_handler，理由见 nodes.on_call_model_error
        add("CallModel", call_model, error_handler=on_call_model_error)
        add("ToolExecutor", tool_executor)
        add("HumanApproval", human_approval)
        add("AutoClassify", auto_classify)
        add("OnAgentStop", on_agent_stop)
        # 离线写回锚点：正常流程永远不路由到它（刻意无入边）。bridge 在图不运行时
        # 经 aupdate_state 写状态（中断收尾的半截回复/补合成 ToolMessage、离线压缩
        # 摘要）统一挂此名——CallModel 的条件边 is_use_tool 需要 Runtime 注入而
        # aupdate_state 给不了（必抛 Missing required config key），固定边节点则
        # 免求值；且出边直达 END，写完 next 即空，checkpoint 恒干净。
        add("OfflineFlush", lambda _state: {})

    def _draw_edges(self):
        """添加边"""
        # 串行：Summarizer（当轮就地压缩）→ Preprocess（UserPromptSubmit hook 注入
        # 上下文）→ CallModel。压缩在前，注入永远发生在压缩后的世界里——旧注入块
        # 与 marker 随历史删除，hook 自动全量重建；hook 注入的消息也不会被当轮压掉。
        self.builder.add_edge(START, "Summarizer")
        self.builder.add_edge("Summarizer", "PreprocessMessages")
        self.builder.add_edge("PreprocessMessages", "CallModel")
        self.builder.add_conditional_edges(
            "CallModel",
            is_use_tool,
            {
                "ToolExecutor": "ToolExecutor",
                "HumanApproval": "HumanApproval",
                "AutoClassify": "AutoClassify",
                "OnAgentStop": "OnAgentStop",
            },
        )
        self.builder.add_conditional_edges(
            "ToolExecutor",
            after_tool_executor,
            {"CallModel": "CallModel", "END": END},
        )
        self.builder.add_edge("OfflineFlush", END)

    async def adelete_thread(self, thread_id: str) -> None:
        """
        删除与特定线程 ID 关联的所有检查点和写入记录
        注意：此方法仅在使用checkpointer时有效

        Args:
            thread_id (str): 应删除其检查点的线程 ID
        """
        if self.checkpointer is None:
            raise RuntimeError("当前Agent未启用checkpointer，无法删除线程")
        await self.checkpointer.adelete_thread(thread_id)

    async def aprune_checkpoints_after(self, thread_id: str, checkpoint_id: str) -> int:
        """删除指定 checkpoint_id 之后的所有 checkpoint（用于 rewind 清理旧分支）。

        LangGraph checkpoint_id 使用 UUID6（时间有序），字符串比较可正确判断先后。

        Args:
            thread_id: 线程 ID
            checkpoint_id: 保留此 checkpoint 及之前的所有记录，删除之后的

        Returns:
            删除的 checkpoint 数量
        """
        if self.checkpointer is None:
            logger.warning(
                "[LumiAgent] aprune_checkpoints_after: checkpointer 未配置，跳过清理"
            )
            return 0

        cp = self.checkpointer
        deleted = 0

        if isinstance(cp, AsyncSqliteSaver):
            async with cp.lock, cp.conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM checkpoints"
                    " WHERE thread_id = ? AND checkpoint_ns = '' AND checkpoint_id > ?",
                    (thread_id, checkpoint_id),
                )
                # aiosqlite 的 rowcount 对 DELETE 可能返回 -1，改用 changes()
                await cur.execute("SELECT changes()")
                row = await cur.fetchone()
                deleted = row[0] if row else 0
                await cur.execute(
                    "DELETE FROM writes"
                    " WHERE thread_id = ? AND checkpoint_ns = '' AND checkpoint_id > ?",
                    (thread_id, checkpoint_id),
                )
                await cp.conn.commit()
        elif isinstance(cp, AsyncPostgresSaver):
            async with cp._cursor(pipeline=True) as cur:
                await cur.execute(
                    "DELETE FROM checkpoints"
                    " WHERE thread_id = %s AND checkpoint_ns = '' AND checkpoint_id > %s",
                    (thread_id, checkpoint_id),
                )
                deleted = cur.rowcount if cur.rowcount >= 0 else 0
                await cur.execute(
                    "DELETE FROM checkpoint_writes"
                    " WHERE thread_id = %s AND checkpoint_ns = '' AND checkpoint_id > %s",
                    (thread_id, checkpoint_id),
                )
                # checkpoint_blobs 以 (thread_id, channel, version) 为键，
                # 无法按 checkpoint_id 精确清理；孤立 blob 无害，
                # 在 adelete_thread 时会一并删除。
        elif isinstance(cp, InMemorySaver):
            # 仅清理根 namespace（""）
            ns_dict = cp.storage.get(thread_id, {}).get("", {})
            to_remove = [cid for cid in ns_dict if cid > checkpoint_id]
            deleted = len(to_remove)
            for cid in to_remove:
                del ns_dict[cid]
            for key in list(cp.writes.keys()):
                if key[0] == thread_id and key[1] == "" and key[2] > checkpoint_id:
                    del cp.writes[key]
        else:
            logger.error(
                "[LumiAgent] aprune_checkpoints_after: 不支持的 checkpointer 类型 %s，"
                "旧 checkpoint 数据将不会被清理",
                type(cp).__name__,
            )
            return -1

        return deleted

    async def aclose(self) -> None:
        """关闭 checkpointer 连接，释放资源"""
        await close_checkpointer(self.checkpointer)


async def close_checkpointer(checkpointer: BaseCheckpointSaver | None) -> None:
    """关闭 checkpointer 底层连接，释放资源（LumiAgent 与 cron Scheduler 共用）。"""
    if checkpointer is None:
        return
    conn = getattr(checkpointer, "conn", None)
    if conn is not None and hasattr(conn, "close"):
        try:
            await conn.close()
        except Exception as e:
            logger.error(f"关闭 checkpointer 连接失败: {e}")


CHECKPOINT_AES_KEY_ENV = "LUMI_CHECKPOINT_AES_KEY"

# 进 checkpoint 的自定义类型白名单。langgraph 默认「警告但放行」任意类型，日志每次
# 读盘刷一条 "will be blocked in a future version"；显式列出后 msgpack 转严格模式：
# 内置安全类型 + 这里列的才允许反序列化，其余被拦下并记 warning（值变空，不抛异常）。
# 现存 checkpoint 库里扫出来的自定义类型只有 Todo 一个。
# **新增会落进 state 的自定义类型时必须加到这里**，否则那个字段读回来是空的。
_ALLOWED_CHECKPOINT_TYPES = (Todo,)


def _checkpoint_serde() -> SerializerProtocol:
    """checkpoint 序列化器：类型白名单恒生效，配了密钥再叠一层 AES 加密。

    加密开关是 ``LUMI_CHECKPOINT_AES_KEY``（16/24/32 字节）——checkpoints.db 存的是
    整段对话原文。密钥只认环境变量、不进 ``lumi.json``：钥匙和锁放同一个 ``~/.lumi``
    目录等于没锁。存量明文 checkpoint 仍可读（``EncryptedSerializer.loads_typed`` 按
    类型标记分流，无 cipher 标记的走明文），所以开关随时可开、不需要迁移；关掉则新
    写入恢复明文、已加密的读不回来。加密依赖可选包：
    ``pip install 'lumi-harness[encryption]'``。
    """
    base = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_CHECKPOINT_TYPES)
    key = os.environ.get(CHECKPOINT_AES_KEY_ENV, "").encode()
    if not key:
        return base
    # 自己校验长度：langgraph 只在读它自己那个 env 变量时校验，显式传 key 会跳过，
    # 于是坏密钥一路装到第一次落盘才炸（服务照常起来，之后每轮都写不进 checkpoint）
    if len(key) not in (16, 24, 32):
        raise ValueError(
            f"{CHECKPOINT_AES_KEY_ENV} 须是 16 / 24 / 32 字节，当前 {len(key)} 字节"
        )
    from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

    return EncryptedSerializer.from_pycryptodome_aes(serde=base, key=key)


async def create_checkpointer(
    checkpoint: CheckpointMode | None = None,
) -> BaseCheckpointSaver | None:
    """根据指定模式创建 checkpointer 实例

    Args:
        checkpoint: 检查点模式，可选值为 "memory"、"sqlite"、"postgres"、None。
                    None 表示不使用 checkpointer。

    Returns:
        BaseCheckpointSaver 实例，或 None（不使用 checkpointer）
    """
    if checkpoint is None:
        return None

    match checkpoint:
        case "sqlite":
            checkpoint_dir = GlobalConfigManager.load().get_checkpoint_dir()
            db_path = str(checkpoint_dir / "checkpoints.db")
            try:
                checkpoint_dir.mkdir(parents=True, exist_ok=True)
                conn = await aiosqlite.connect(db_path)
                checkpointer = AsyncSqliteSaver(conn, serde=_checkpoint_serde())
                await checkpointer.setup()
                return checkpointer
            except Exception as e:
                raise RuntimeError(
                    f"SQLite checkpointer 初始化失败 ({db_path}): {e}"
                ) from e
        case "postgres":
            uri = get_config().config.agents.postgres_uri
            if not uri:
                raise ValueError(
                    "checkpoint 设为 'postgres' 时必须配置 agents.postgres_uri"
                )
            conn = await AsyncConnection.connect(
                uri, autocommit=True, prepare_threshold=0, row_factory=dict_row
            )
            checkpointer = AsyncPostgresSaver(conn=conn, serde=_checkpoint_serde())
            await checkpointer.setup()
            return checkpointer
        case _:
            return InMemorySaver(serde=_checkpoint_serde())


async def create_agent(
    tools: list | None = None,
    system_prompt: str | None = None,
    model_name: str | None = None,
    checkpoint: CheckpointMode | None = None,
    permission_engine: PermissionEngine | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    project_dir: Path | None = None,
    enable_memory: bool = False,
) -> tuple["LumiAgent", LumiAgentContext]:
    """创建 LumiAgent 及其上下文的工厂函数

    所有参数均可选，未指定时从配置文件读取默认值。
    子 agent 场景可传入自定义参数并设置 checkpoint=None 跳过持久化。

    Args:
        tools: 工具列表，默认从注册表加载全部工具
        system_prompt: 系统提示词，默认从配置文件加载
        model_name: 模型名称，默认使用 active 供应商模型（无则 env 默认）
        checkpoint: 检查点模式，None 表示不使用 checkpointer
        permission_engine: 权限引擎实例，传入时复用（子 agent 场景），
                           None 时新建
        checkpointer: 直接复用已有 checkpointer 实例（如 cron 调度器常驻连接），
                      优先于 checkpoint 模式；调用方负责其生命周期
        project_dir: 权限引擎绑定的项目根目录（None 时用进程 cwd）。项目随会话
                     绑定后由调用方显式传入，新建引擎时不再依赖进程 cwd。
                     hooks 已改为按会话经 contextvar 注入，此处不再加载。
        enable_memory: 是否为本 agent 启用持久记忆（默认 False，opt-in）。持久记忆有副作用
                       （写磁盘 / 改系统提示词 / 注入上下文 / 写入免审批），故只有面向用户的
                       对话入口（bridge）显式传 True；子 agent、workflow、cron 等天然不带记忆。

    Returns:
        (agent, context) 元组
    """
    config = get_config()

    if tools is None:
        # 默认等冷池就位（cron 等单发调用方没有下一轮可自愈）；分层加载该项目的 MCP
        tools = await get_tools(project_dir=project_dir)
    if system_prompt is None:
        system_prompt = config.load_system_prompt(project_dir)
    # provider 随 model_name 同源：跟随 active 时取 active profile id；显式传名
    # （子 agent 配置的裸模型名）无从得知归属，留空走按名反查
    provider = ""
    if model_name is None:
        resolved = provider_store.resolve()
        model_name = resolved.model
        provider = resolved.provider

    # 启用记忆：确保记忆目录存在，并把记忆行为说明追加到主 agent 系统提示词尾部。
    # 记忆目录按会话项目根（project_dir，未传则进程 cwd）隔离，与权限引擎同源。
    if enable_memory:
        from lumi.agents.memory import build_memory_instructions, ensure_memory_dir

        # 记忆目录 key 由 memory_dir 内部 resolve，此处不必重复 resolve。
        mem_dir = ensure_memory_dir(project_dir or Path.cwd())
        instructions = build_memory_instructions(mem_dir)
        system_prompt = (
            f"{system_prompt}\n\n{instructions}" if system_prompt else instructions
        )

    # 复用或新建权限引擎（项目根随会话绑定，调用方未传则退回进程 cwd）
    if permission_engine is None:
        permission_engine = PermissionEngine(project_dir or Path.cwd())

    if checkpointer is None:
        checkpointer = await create_checkpointer(checkpoint)
    agent = LumiAgent(checkpointer=checkpointer)
    context = LumiAgentContext(
        tools=tools,
        project_dir=project_dir,
        system_prompt=system_prompt,
        model_name=model_name,
        provider=provider,
        permission_engine=permission_engine,
        memory_enabled=enable_memory,
    )
    return agent, context

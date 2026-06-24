from pathlib import Path

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from lumi.agents.core.base_graph import BaseGraph
from lumi.agents.core.nodes import (
    after_tool_executor,
    auto_classify,
    call_model,
    human_approval,
    is_use_tool,
    on_agent_stop,
    policy_reject,
    preprocess_messages,
    summarizer,
    tool_executor,
)
from lumi.agents.core.state import LumiAgentContext, LumiAgentState
from lumi.agents.permissions.engine import PermissionEngine
from lumi.agents.tools import get_tools
from lumi.models import provider_store
from lumi.utils.config import CheckpointMode, GlobalConfigManager
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


class LumiAgent(BaseGraph):
    state_cls = LumiAgentState

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
    ):
        """
        初始化 LumiAgent

        Args:
            checkpointer: checkpointer 实例，用于状态持久化，默认为 None（不使用）
        """
        self.checkpointer = checkpointer

        super().__init__()
        # 直接编译 graph
        if self.checkpointer is not None:
            self.graph = self.builder.compile(checkpointer=self.checkpointer)
        else:
            self.graph = self.builder.compile()

    def _draw_nodes(self):
        """添加节点"""
        self.builder.add_node("PreprocessMessages", preprocess_messages)
        self.builder.add_node("Summarizer", summarizer)
        self.builder.add_node("CallModel", call_model)
        self.builder.add_node("ToolExecutor", tool_executor)
        self.builder.add_node("HumanApproval", human_approval)
        self.builder.add_node("AutoClassify", auto_classify)
        self.builder.add_node("PolicyReject", policy_reject)
        self.builder.add_node("OnAgentStop", on_agent_stop)

    def _draw_edges(self):
        """添加边"""
        self.builder.add_edge(START, "PreprocessMessages")
        self.builder.add_edge("PreprocessMessages", "CallModel")
        self.builder.add_edge("PreprocessMessages", "Summarizer")
        self.builder.add_edge("Summarizer", END)
        self.builder.add_conditional_edges(
            "CallModel",
            is_use_tool,
            {
                "ToolExecutor": "ToolExecutor",
                "HumanApproval": "HumanApproval",
                "AutoClassify": "AutoClassify",
                "PolicyReject": "PolicyReject",
                "OnAgentStop": "OnAgentStop",
                # END 保留给 is_use_tool 的防御性路径（消息为空/None）
                "END": END,
            },
        )
        self.builder.add_conditional_edges(
            "ToolExecutor",
            after_tool_executor,
            {"CallModel": "CallModel", "END": END},
        )

    async def adelete_thread(self, thread_id: str) -> None:
        """
        删除与特定线程 ID 关联的所有检查点和写入记录
        注意：此方法仅在使用checkpointer时有效

        Args:
            thread_id (str): 应删除其检查点的线程 ID
        """
        if self.checkpointer is None:
            raise RuntimeError("当前Agent未启用checkpointer，无法删除线程")

        if hasattr(self.checkpointer, "adelete_thread"):
            await self.checkpointer.adelete_thread(thread_id)
        else:
            raise RuntimeError("checkpointer 不支持 adelete_thread 方法")

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
                checkpointer = AsyncSqliteSaver(conn)
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
            checkpointer = AsyncPostgresSaver(conn=conn)
            await checkpointer.setup()
            return checkpointer
        case _:
            return InMemorySaver()


async def create_agent(
    tools: list | None = None,
    system_prompt: str | None = None,
    model_name: str | None = None,
    checkpoint: CheckpointMode | None = None,
    permission_engine: PermissionEngine | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    project_dir: Path | None = None,
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

    Returns:
        (agent, context) 元组
    """
    config = get_config()

    if tools is None:
        tools = await get_tools()
    if system_prompt is None:
        system_prompt = config.load_system_prompt()
    if model_name is None:
        model_name = provider_store.resolve().model

    # 复用或新建权限引擎（项目根随会话绑定，调用方未传则退回进程 cwd）
    if permission_engine is None:
        try:
            permission_engine = PermissionEngine(project_dir or Path.cwd())
        except Exception:
            logger.error("权限引擎创建失败，将以无权限模式运行", exc_info=True)

    if checkpointer is None:
        checkpointer = await create_checkpointer(checkpoint)
    agent = LumiAgent(checkpointer=checkpointer)
    context = LumiAgentContext(
        tools=tools,
        system_prompt=system_prompt,
        model_name=model_name,
        permission_engine=permission_engine,
    )
    return agent, context

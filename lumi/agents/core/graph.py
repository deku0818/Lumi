from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START

import aiosqlite
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from lumi.agents.base.graph import BaseGraph
from lumi.agents.core.node import (
    after_tool_executor,
    call_model,
    extract_structured_output,
    human_approval,
    is_use_tool,
    preprocess_messages,
    summarizer,
    tool_executor,
)
from lumi.agents.core.scheme import LumiAgentContext, LumiAgentState
from lumi.agents.tools import get_tools
from lumi.agents.tools.permissions.engine import PermissionEngine
from lumi.utils.config import CheckpointMode, GlobalConfigManager
from lumi.utils.logger import logger
from lumi.utils.model_manager import get_default_model_name
from lumi.utils.read_config import get_config


class LumiAgent(BaseGraph):
    state_cls = LumiAgentState

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
    ):
        """
        初始化SimpleAgent

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
        self.builder.add_node("ExtractStructuredOutput", extract_structured_output)

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
                "ExtractStructuredOutput": "ExtractStructuredOutput",
                "END": END,
            },
        )
        self.builder.add_conditional_edges(
            "ToolExecutor",
            after_tool_executor,
            {"CallModel": "CallModel", "END": END},
        )
        self.builder.add_edge("ExtractStructuredOutput", END)

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

    async def aclose(self) -> None:
        """关闭 checkpointer 连接，释放资源"""
        if self.checkpointer is None:
            return
        conn = getattr(self.checkpointer, "conn", None)
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
) -> tuple["LumiAgent", LumiAgentContext]:
    """创建 LumiAgent 及其上下文的工厂函数

    所有参数均可选，未指定时从配置文件读取默认值。
    子 agent 场景可传入自定义参数并设置 checkpoint=None 跳过持久化。

    Args:
        tools: 工具列表，默认从注册表加载全部工具
        system_prompt: 系统提示词，默认从配置文件加载
        model_name: 模型名称，默认使用环境变量配置
        checkpoint: 检查点模式，None 表示不使用 checkpointer
        permission_engine: 权限引擎实例，传入时复用（子 agent 场景），
                           None 时新建

    Returns:
        (agent, context) 元组
    """
    config = get_config()

    if tools is None:
        tools = await get_tools()
    if system_prompt is None:
        system_prompt = config.load_system_prompt()
    if model_name is None:
        model_name = get_default_model_name()

    # 复用或新建权限引擎
    if permission_engine is None:
        try:
            permission_engine = PermissionEngine(Path.cwd())
        except Exception:
            logger.error("权限引擎创建失败，将以无权限模式运行", exc_info=True)

    checkpointer = await create_checkpointer(checkpoint)
    agent = LumiAgent(checkpointer=checkpointer)
    context = LumiAgentContext(
        tools=tools,
        system_prompt=system_prompt,
        model_name=model_name,
        permission_engine=permission_engine,
    )
    return agent, context


if __name__ == "__main__":
    import asyncio

    from langchain_core.messages import HumanMessage

    async def main():
        # 加载所有已注册的工具
        tools = await get_tools()
        print(f"已加载 {len(tools)} 个工具: {[t.name for t in tools]}")

        agent = LumiAgent()
        context = LumiAgentContext(
            tools=tools,
            system_prompt="You are a helpful assistant.",
            model_name="qwen3-max",
        )
        inputs = {
            "messages": [
                HumanMessage(
                    content="帮我写一个python脚本呗，只要hello word 即可，在/Users/y-pc/Cocoon 目录下"
                )
            ],
            "tool_mode": "auto",
        }
        response = await agent.graph.ainvoke(inputs, context=context)
        print(response)

    asyncio.run(main())

from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START

from lumi.agents.base.graph import BaseGraph
from lumi.agents.core.node import (
    call_model,
    extract_structured_output,
    human_approval,
    is_use_tool,
    preprocess_messages,
    summarizer,
    tool_executor,
)
from lumi.agents.core.scheme import LumiAgentState
from lumi.utils.read_config import get_config


class LumiAgent(BaseGraph):
    state_cls = LumiAgentState

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
        prompt_caching_ttl: Literal["5m", "1h"] | None = None,
    ):
        """
        初始化SimpleAgent

        Args:
            checkpointer: checkpointer 实例，用于状态持久化，默认为 None（不使用）
            tools (list): 工具列表
            system_prompt (str | None): 系统提示词，默认从配置文件加载（SOUL.md + GUARDRAILS.md + AGENTS.md）
            model_name (str): 指定使用的模型名称，默认使用环境变量配置
            prompt_caching_ttl: （仅对Anthropic模型生效）Anthropic prompt caching TTL，设置即开启（'5m' 或 '1h'），None 不开启
            **llm_params: LLM的其他参数，会传递给tool_call_chain
        """
        self.checkpointer = checkpointer
        # 如果未指定 prompt_caching_ttl，则从配置加载
        if prompt_caching_ttl is None:
            self.prompt_caching_ttl = get_config().config.agents.prompt_caching_ttl
        else:
            self.prompt_caching_ttl = prompt_caching_ttl

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
        self.builder.add_edge("ToolExecutor", "CallModel")
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


if __name__ == "__main__":
    import asyncio

    from langchain_core.messages import HumanMessage

    from lumi.agents.core.scheme import LumiAgentContext
    from lumi.agents.tools import get_tools

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

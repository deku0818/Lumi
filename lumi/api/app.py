"""FastAPI LangGraph 原始事件流 API"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from lumi.agents.base.response_service import astream_raw_events
from lumi.agents.core.graph import LumiAgent
from lumi.agents.core.scheme import LumiAgentContext
from lumi.agents.tools import get_tools
from lumi.agents.tools.session import get_session_manager
from lumi.utils.model_manager import DEFAULT_MODEL_NAME
from lumi.utils.read_config import get_config
from lumi.utils.thread_id import generate_thread_id


class LangGraphRequest(BaseModel):
    input: dict | None = None
    resume: dict | str | None = None
    configurable: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动
    tools = await get_tools()
    checkpointer = MemorySaver()
    agent = LumiAgent(checkpointer=checkpointer)
    context = LumiAgentContext(
        tools=tools,
        system_prompt=get_config().load_system_prompt(),
        model_name=DEFAULT_MODEL_NAME,
    )
    app.state.agent = agent
    app.state.context = context
    yield
    # 关闭
    await get_session_manager().close_all()


app = FastAPI(lifespan=lifespan)


@app.post("/api/agent/langgraph")
async def langgraph_stream(body: LangGraphRequest):
    configurable = dict(body.configurable)

    # thread_id 不存在时自动生成
    if "thread_id" not in configurable:
        configurable["thread_id"] = generate_thread_id()

    # 构造 input
    if body.resume is not None:
        input_data = Command(resume=body.resume)
    else:
        input_data = body.input

    config = RunnableConfig(configurable=configurable)

    graph = app.state.agent.graph

    return StreamingResponse(
        astream_raw_events(graph, input_data, config, context=app.state.context),
        media_type="text/event-stream",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090)

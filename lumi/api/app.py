"""FastAPI LangGraph 原始事件流 API"""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Command
from pydantic import BaseModel

from lumi.agents.base.response_service import astream_raw_events
from lumi.agents.core.graph import create_agent
from lumi.agents.tools.session import get_session_manager
from lumi.utils.logger import logger
from lumi.utils.thread_id import generate_thread_id


class LangGraphRequest(BaseModel):
    input: dict | None = None
    resume: dict | str | None = None
    configurable: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：注入 config.yaml 中的 env 环境变量
    from lumi.utils.config import get_config

    try:
        get_config().apply_env()
    except Exception as e:
        logger.warning(f"注入环境变量失败: {e}")
        raise

    agent, context = await create_agent(
        checkpoint=get_config().config.agents.checkpoint,
    )
    app.state.agent = agent
    app.state.context = context

    # 初始化定时任务子系统
    try:
        from lumi.agents.cron.delivery import APIDelivery, DeliveryManager
        from lumi.agents.cron.job_store import JobStore
        from lumi.agents.cron.run_log import RunLog
        from lumi.agents.cron.scheduler import Scheduler
        from lumi.agents.tools.providers.cron import init_cron_tool
        from lumi.utils.config.global_manager import GLOBAL_CONFIG_DIR

        cron_dir = GLOBAL_CONFIG_DIR / "cron"
        job_store = JobStore(cron_dir / "jobs.json")
        run_log = RunLog(cron_dir / "runs")
        delivery = DeliveryManager()
        api_delivery = APIDelivery()
        delivery.register(api_delivery)
        scheduler = Scheduler(job_store, run_log, delivery)
        init_cron_tool(scheduler, job_store, run_log)
        await scheduler.start()
        app.state.scheduler = scheduler
        app.state.delivery = delivery
        app.state.api_delivery = api_delivery
        logger.info("[API] 定时任务子系统已启动")
    except Exception:
        logger.error("[API] 定时任务子系统启动失败，cron 功能不可用", exc_info=True)
        app.state.scheduler = None
        app.state.delivery = None
        app.state.api_delivery = None

    yield

    # 关闭：优雅停止定时任务子系统
    if app.state.scheduler:
        await app.state.scheduler.stop()
    if app.state.delivery:
        await app.state.delivery.close_all()
    await agent.aclose()
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


@app.get("/api/cron/events")
async def cron_events() -> StreamingResponse:
    """SSE 端点，订阅定时任务执行结果。"""
    from lumi.agents.cron.delivery import APIDelivery

    api_delivery: APIDelivery = app.state.api_delivery

    async def event_stream():
        async for msg in api_delivery.subscribe():
            data = json.dumps(msg, ensure_ascii=False)
            yield f"data: {data}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090)

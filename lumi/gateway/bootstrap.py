"""进程级 bootstrap：所有 channel 共享的一次性启动/收尾。

不绑定任何具体传输（无 FastAPI 依赖）。FastAPI 的 lifespan、独立进程 channel
（如未来的 IM long-polling 进程）都包一层 ``async with gateway_process():`` 复用同一份
逻辑：第三方库补丁 / 配置生效 / 模型目录刷新 / cron 子系统 / 后台任务广播接线，
退出时统一收尾共享运行时。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from lumi.agents.cron.delivery import DeliveryManager
from lumi.agents.cron.runtime import setup_cron
from lumi.agents.runtime.bg_tasks import get_task_registry
from lumi.gateway.bridge import shutdown_shared_runtime
from lumi.gateway.broadcast import hub
from lumi.gateway.cron_rpc import set_cron_runtime
from lumi.utils.logger import logger


@asynccontextmanager
async def gateway_process():
    """进程级运行时上下文：进入时启动共享子系统，退出时收尾。任何 channel 复用。"""
    from lumi.models import catalog
    from lumi.utils.read_config import get_config

    get_config().apply_env()

    # 后台刷新 models.dev 模型目录（思考能力 + context_length 数据源）。
    # 必须持强引用：事件循环只弱引用 task，不留引用可能在协程首次挂起前被 GC。
    catalog_task = asyncio.create_task(catalog.refresh())

    # 初始化定时任务子系统（按工作目录隔离）
    cron_runtime = None
    try:
        delivery = DeliveryManager()
        delivery.register(hub.delivery)
        cron_runtime = setup_cron(delivery, on_job_status=hub.on_cron_job_status)
        set_cron_runtime(cron_runtime)
        await cron_runtime.scheduler.start()
        logger.info("[gateway] 定时任务子系统已启动")
    except Exception:
        logger.warning(
            "[gateway] 定时任务子系统启动失败，cron 功能不可用", exc_info=True
        )

    # 后台任务变更 → 广播 bg_tasks.update，驱动前端实时刷新
    get_task_registry().set_on_change(hub.on_bg_task_change)

    # MCP 池后台加载完成 → 广播 mcp.status（失败项前端 toast / 面板徽标数据源）
    from lumi.agents.tools.providers.mcp import set_on_pool_loaded

    set_on_pool_loaded(hub.on_mcp_status)

    try:
        yield
    finally:
        if not catalog_task.done():
            catalog_task.cancel()
        get_task_registry().set_on_change(None)
        set_on_pool_loaded(None)
        if cron_runtime is not None:
            await cron_runtime.scheduler.stop()
        # 进程级共享运行时（MCP / shell 会话）只在进程退出时关闭一次，
        # 不能随单条连接的 bridge.close() 拆除
        await shutdown_shared_runtime()

"""进程级 bootstrap：所有 channel 共享的一次性启动/收尾。

不绑定任何具体传输（无 FastAPI 依赖）。FastAPI 的 lifespan、独立进程 channel
（如未来的 IM long-polling 进程）都包一层 ``async with gateway_process():`` 复用同一份
逻辑：第三方库补丁 / 配置生效 / 模型目录刷新 / cron 子系统 / 后台任务广播接线，
退出时统一收尾共享运行时。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from lumi.agents.core.run_control import drain_all, wait_drained
from lumi.agents.cron.delivery import DeliveryManager
from lumi.agents.cron.runtime import setup_cron
from lumi.agents.runtime.bg_tasks import get_task_registry
from lumi.gateway.bridge import shutdown_shared_runtime
from lumi.gateway.broadcast import hub
from lumi.gateway.cron_rpc import set_cron_runtime
from lumi.utils.logger import logger

# 停机时留给在跑的图跑完当前 super-step 的**上限**（真停完就立刻返回，不空等）。
# 一个 super-step 通常是一次模型调用或一批工具，超时就硬切——这不是等它跑完整轮。
_DRAIN_GRACE_SECONDS = 3.0


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
        # 任务增删改（agent 工具 / desktop UI 两条路都经此 store）→ 广播 cron.jobs，
        # 驱动前端实时刷新列表，无需手动 Ctrl+R
        cron_runtime.job_store.set_on_change(hub.on_cron_jobs_changed)
        # cron 执行走 AgentBridge 流式 runner：产出事件 publish 给观测者（桌面打开运行中的
        # cron 线程即实时看到流）。未注入时 Scheduler fallback 到 ainvoke（TUI / 测试）。
        from lumi.gateway.cron_stream import build_cron_stream_runner

        cron_runtime.scheduler.set_stream_runner(build_cron_stream_runner(hub))
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
        # 先请求在跑的图运行优雅停机，再拆子系统：drain 让它们停在 super-step
        # 边界（checkpoint 完整、next 指向待执行节点，重连传 None 即续跑），
        # 而不是被随后的进程退出硬切在节点半途。宽限期是上限不是定额——停完即返回；
        # 超时没停完的照旧被取消，那条路本来就有 persist_partial_reply 兜底。
        if drain_all("gateway shutdown"):
            await wait_drained(_DRAIN_GRACE_SECONDS)
        if not catalog_task.done():
            catalog_task.cancel()
        get_task_registry().set_on_change(None)
        set_on_pool_loaded(None)
        if cron_runtime is not None:
            await cron_runtime.scheduler.stop()
        # 进程级共享运行时（MCP / shell 会话）只在进程退出时关闭一次，
        # 不能随单条连接的 bridge.close() 拆除
        await shutdown_shared_runtime()

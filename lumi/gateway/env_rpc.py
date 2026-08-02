"""环境工具箱 RPC：状态查询 + 安装（后台执行，进度经广播扇出）。

安装是分钟级下载，不能挂在 RPC 响应里：env_install 立即返回 started，进度走
env.progress（节流到整数百分比变化），结束后广播 env.state 全量状态——所有
连接同步刷新，与 bg_tasks 的「快照广播、前端过滤」同范式。

两个 target 语义不同：env.progress 的 target 恒为具体组件名（装齐时逐工具下发，
"all" 永不上 progress 线，前端各行各显示各的）；env.state 的 target 是本次请求的
目标（装齐 = "all"），供多面板判断这次安装与自己是否相关。
"""

from __future__ import annotations

import asyncio

from lumi.gateway import toolbox
from lumi.gateway.broadcast import hub
from lumi.utils.logger import logger

ENV_METHODS = frozenset({"env_status", "env_install"})

# lark-cli 机器级、feishu-skills 项目级（需 project 参数），由渠道体检的 fix 按钮
# 触发；officecli（ALL_TOOLS 内）另有预览面板的就地安装按钮入口
_TARGETS = frozenset({"all", "lark-cli", "feishu-skills", *toolbox.ALL_TOOLS})

# 进行中的安装目标（空串 = 空闲）。任一安装进行中即拒绝新安装（全局互斥）：
# target 之间有重叠（all ⊃ uv/rg/node，lark-cli 经 npm 写进 node 树），并行会让
# 两条线程写同一二进制/rmtree 同一棵树。单值即互斥不变量本身，也直接下发给 env_status
# 供前端恢复进行中态（对齐 get_mcp_status 的 loading 范式）。
_installing = ""
# 事件循环只弱引用 task，自持引用避免分钟级安装任务在执行中被 GC（同 BroadcastHub._spawn）
_tasks: set[asyncio.Task] = set()


async def dispatch_env(method: str, params: dict) -> dict:
    """执行一个环境 RPC 方法（method 已确认属于 ENV_METHODS）。"""
    global _installing
    if method == "env_status":
        status = await asyncio.to_thread(toolbox.status_all)
        status["installing"] = _installing
        return status

    target = params.get("target") or "all"
    if target not in _TARGETS:
        raise ValueError(f"未知安装目标: {target}")
    if _installing:
        return {"started": False}
    _installing = target
    task = asyncio.create_task(_run_install(target, params.get("project") or ""))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"started": True}


async def _run_install(target: str, project: str = "") -> None:
    global _installing
    loop = asyncio.get_running_loop()

    def progress_for(wire_target: str) -> toolbox.ProgressFn:
        last = None

        def progress(phase: str, pct: float | None) -> None:
            # 下载回调按 64KB 块触发，节流到「阶段变化或整数百分比前进」才广播
            nonlocal last
            pct_int = -1 if pct is None else int(pct * 100)
            if (phase, pct_int) == last:
                return
            last = (phase, pct_int)
            asyncio.run_coroutine_threadsafe(
                hub.delivery.send_event(
                    "env.progress",
                    {"target": wire_target, "phase": phase, "percent": pct_int},
                ),
                loop,
            )

        return progress

    error = ""
    try:
        if target == "lark-cli":
            await asyncio.to_thread(toolbox.install_lark_cli, progress_for(target))
        elif target == "feishu-skills":
            await asyncio.to_thread(
                toolbox.sync_lark_skills, progress_for(target), project
            )
        else:
            # all = 逐个装缺失的工具（核心 + 可选，与环境页两栏一致），进度按工具名
            # 广播（前端各行各显示各的）；「已装的跳过」仍单源在 install_missing 里
            # （officecli 单目标同走此路）。逐个调用使其并行探测退化为串行——探测毫秒级、
            # 下载分钟级，不值得为此给它加 progress 工厂参数
            names = toolbox.ALL_TOOLS if target == "all" else (target,)
            for name in names:
                await asyncio.to_thread(
                    toolbox.install_missing, progress_for(name), (name,)
                )
    except Exception as e:
        logger.warning(f"安装 {target} 失败: {e}")
        error = str(e)
    finally:
        _installing = ""

    state = await asyncio.to_thread(toolbox.status_all)
    # 带上本次安装的 target：多面板各自订阅（环境页 / 渠道体检），无 target 时
    # 一处的安装结束会误清另一处的进度、提前重跑无关体检
    state["target"] = target
    if error:
        state["error"] = {"target": target, "message": error}
    await hub.delivery.send_event("env.state", state)

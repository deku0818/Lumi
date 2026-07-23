"""cron 执行的流式 runner：用 AgentBridge 跑 job，把 BridgeEvent 逐条直播给观测者。

注入进 Scheduler（``set_stream_runner``）。调度器是唯一驱动者——它 ``await runner`` 把流
抽干到底，无论有没有观测者都跑完；runner 每产出一个事件就 ``publish`` 给该 thread 的
观测者（0 个时空操作）。用全套 AgentBridge：事件转换、workspace_dir metadata、记忆全部
复用，cron 因此成为一等会话。cron 线程不触发 autoDream（见 memory.dream 的 cron 前缀闸）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from lumi.agents.cron.job_runner import extract_output
from lumi.gateway.protocol import bridge_event_to_wire


def build_cron_stream_runner(hub) -> Callable[[str, str], Awaitable[str]]:
    """构造注入 Scheduler 的流式 runner；``hub`` 提供 publish_thread_event。"""

    async def runner(prompt: str, thread_id: str) -> str:
        # 延迟 import 避免 gateway.bridge 在 bootstrap 早期成环
        from lumi.gateway.bridge import AgentBridge, EventKind

        bridge = AgentBridge()
        # project_dir="" → 退回进程 cwd（cron 项目，与旧 create_agent 路径一致）；
        # wait_mcp=True：单发执行无下一轮自愈，须等 MCP 池就位。
        await bridge.initialize(project_dir="", wait_mcp=True)
        bridge.switch_thread(thread_id)
        error = ""
        try:
            # privileged：cron 无交互审批通道。synthetic：job prompt 是机器注入指令、
            # 不作用户气泡显示（items: []），但助手/工具事件照常直播。
            async for evt in bridge.stream_response(
                prompt, tool_mode="privileged", synthetic=True
            ):
                # 零观测者（无人在看这条 cron 线程）时不白建 wire 帧——token 级 delta 每 run
                # 成百上千，构帧只在有人观测时才值得。错误检测独立于观测，恒执行。
                if hub.has_observers(thread_id):
                    hub.publish_thread_event(
                        thread_id, bridge_event_to_wire(evt, thread_id)
                    )
                if evt.kind == EventKind.ERROR:
                    error = evt.error or "cron 执行出错"
            # stream_response 把异常吞成 ERROR 事件、不抛；这里补抛，使 scheduler 如实记
            # failed（而非误记 success、且错误计数被清零致该重试的也不重试）。瞬态网络错已
            # 由 bridge 内部重试过（MAX_STREAM_RETRIES），走到这里即持久失败。
            if error:
                raise RuntimeError(error)
            msgs = await bridge.snapshot_messages()
            return extract_output({"messages": msgs})
        finally:
            await bridge.close()

    return runner

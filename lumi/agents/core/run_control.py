"""图运行的协作式停机（drain）。

LangGraph 的 ``RunControl`` 让运行在 **super-step 边界**停下：上一步的写入已落
checkpoint、下一批任务还没开跑，停下时 state 干净、``next`` 指向待执行节点，之后
传 ``None`` 即可从 checkpoint 续跑。与硬 cancel 的分工别混：

- cancel（用户按停）要的是**立刻**，代价是可能落在节点半途（半截回复 / 未应答的
  tool_call），靠 ``persist_partial_reply`` + ``_recover_stale_state`` 事后修；
- drain 要的是**干净**，代价是得等当前 super-step 跑完——切不断正在流的模型调用。
  进程要重启（``lumi serve`` 收到停机信号 / sidecar 被换代）时用它。

注入方式绕了个弯：``astream_events(version="v2")`` **不转发** ``control=``（v1/v2
只把 version 和 **kwargs 透传给父类，具名参数在签名里被吃掉，只有 v3 转发），而
bridge 锁死 v2。好在 ``loop.control`` 取的是 ``control or parent_runtime.control``，
parent runtime 可经 config 注入——代价是用到 LangGraph 私有常量
``CONFIG_KEY_RUNTIME``。这条路由 ``tests/test_drain.py`` 锁住：哪天它不通，那个
测试会红，而不是 drain 静默失效（停机时谁都不会注意到少了一次优雅退出）。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from time import monotonic

from langchain_core.runnables import RunnableConfig
from langgraph._internal._constants import CONFIG_KEY_RUNTIME
from langgraph.pregel.main import DEFAULT_RUNTIME
from langgraph.runtime import RunControl

from lumi.utils.logger import logger

# 进程内所有在跑的图运行。RunControl 带 __slots__ 不可弱引用，故显式登记 / 注销。
_active: set[RunControl] = set()


def with_run_control(config: RunnableConfig, control: RunControl) -> RunnableConfig:
    """返回带 drain 控制的 config 副本（不改原 config）。

    只改 control 一个字段：LangGraph 把 config 里的 runtime 当**父 runtime**，
    ``store`` 等字段会连带生效（``main.py`` 取 store 时优先用它）。凭空造一个
    ``Runtime(control=...)`` 等于用一整个对象传一个 bit，哪天给 LumiAgent 配了
    store，这里会静默把它抹掉。故在既有 runtime（没有就是 DEFAULT_RUNTIME）上
    replace。``context`` 仍走 ``astream_events(context=...)``，由
    ``parent_runtime.merge(runtime)`` 合并，节点侧读到的不受影响。
    """
    configurable = config.get("configurable", {})
    parent = configurable.get(CONFIG_KEY_RUNTIME, DEFAULT_RUNTIME)
    return {
        **config,
        "configurable": {
            **configurable,
            CONFIG_KEY_RUNTIME: replace(parent, control=control),
        },
    }


def register(control: RunControl) -> None:
    _active.add(control)


def unregister(control: RunControl) -> None:
    _active.discard(control)


def drain_all(reason: str = "shutdown") -> int:
    """请求全部在跑的图运行在下一个 super-step 边界停下，返回请求到的运行数。

    只发信号不等待，等多久由调用方定（见 :func:`wait_drained`）。
    """
    for control in _active:
        control.request_drain(reason)
    if _active:
        logger.info("[drain] 已请求 %d 个在跑的图运行停机（%s）", len(_active), reason)
    return len(_active)


async def wait_drained(timeout: float) -> bool:
    """等到所有在跑的运行都注销（drain 完成），或到 ``timeout``。返回是否等干净了。

    轮询而非 Event：登记方来自多个线程/渠道，一个跨 loop 共享的 Event 更容易出错，
    而这条路径一辈子只在进程停机时走一次。停机时早一秒返回就是早一秒重启完。
    """
    deadline = monotonic() + timeout
    while _active and monotonic() < deadline:
        await asyncio.sleep(0.05)
    return not _active

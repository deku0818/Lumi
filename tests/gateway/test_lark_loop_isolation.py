"""多机器人共存时 lark WS 事件循环的线程隔离。

回归来源：配了两个飞书 channel 后，两个 ``feishu-ws`` 线程都往
``lark_oapi.ws.client.loop`` 这个**模块级全局**写自己的 loop，后写的赢。先连上的
那个客户端的协程转眼挂到别人的 loop 上，掉线后每 5 秒重连一次、每次都撞
``This event loop is already running``——只有最后启动的机器人活着。

这里锁的是：两个线程各自登记后，SDK 侧读到的 ``loop`` 必须是**本线程**那个。
"""

from __future__ import annotations

import asyncio
import threading

import lark_oapi.ws.client as ws_client

from lumi.gateway.channels.feishu.lark_loop import use_loop_in_this_thread


def _run_bot(name: str, seen: dict, barrier: threading.Barrier) -> None:
    """模拟一个 feishu-ws 线程：建自己的 loop → 登记 → 等对方也登记完 → 读全局。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        use_loop_in_this_thread(loop)
        barrier.wait(timeout=5)  # 两边都登记完再读，制造「后写的赢」的时序
        seen[name] = loop
        # SDK 真正的用法：直接拿全局跑协程，必须落在本线程的 loop 上
        ws_client.loop.run_until_complete(asyncio.sleep(0))
        seen[name + ":resolved_is_own"] = _resolved_loop() is loop
    finally:
        loop.close()


def _resolved_loop():
    """代理背后、当前线程实际指向的那个 loop。

    代理把属性转发给真 loop，故取一个绑定方法的 ``__self__`` 即可反查回本体
    （代理自身不是 loop，不能直接 ``is`` 比较）。
    """
    return ws_client.loop.call_soon.__self__


def test_two_ws_threads_get_their_own_loop():
    seen: dict = {}
    barrier = threading.Barrier(2)
    threads = [
        threading.Thread(target=_run_bot, args=(name, seen, barrier))
        for name in ("botA", "botB")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()

    assert seen["botA:resolved_is_own"], (
        "botA 读到的不是自己的 loop（全局被 botB 覆盖）"
    )
    assert seen["botB:resolved_is_own"], (
        "botB 读到的不是自己的 loop（全局被 botA 覆盖）"
    )
    assert seen["botA"] is not seen["botB"]  # 两个 loop 确实不是同一个


def test_unregistered_thread_falls_back():
    """没登记过的线程仍读到 fallback（原本那个 loop）——打补丁前后行为一致。

    装代理与探测都在**各自的子线程**里做：主线程一旦登记过，thread-local 会在整个
    pytest 会话里留着一个已关闭的 loop，后续用例读到的就不再是 fallback 了。
    """
    installed = threading.Barrier(2)

    def installer():
        loop = asyncio.new_event_loop()
        try:
            use_loop_in_this_thread(loop)  # 顺带确保代理已装
            installed.wait(timeout=5)
        finally:
            loop.close()

    result: dict = {}

    def probe():
        installed.wait(timeout=5)
        result["loop"] = ws_client.loop.call_soon.__self__

    threads = [threading.Thread(target=installer), threading.Thread(target=probe)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()

    fallback = result["loop"]
    assert fallback is not None
    assert not fallback.is_closed(), "fallback 不该是别的线程那个已关闭的 loop"

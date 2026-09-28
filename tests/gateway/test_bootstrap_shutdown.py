"""gateway_process 退出收尾。"""

from __future__ import annotations

import asyncio

import pytest

from lumi.agents.tools.providers.mcp import pool
from lumi.gateway import bootstrap
from lumi.gateway.session_registry import registry


@pytest.fixture(autouse=True)
def _restore_mcp_latch(monkeypatch):
    """shutdown_shared_runtime 会落进程级关停闩：测试结束复原，不殃及后续用例。"""
    monkeypatch.setattr(pool, "_pools", {})
    monkeypatch.setattr(pool, "_shutting_down", False)


class _DetachedSession:
    closed = False

    async def aclose(self) -> None:
        self.closed = True


async def _noop() -> None:
    return None


@pytest.fixture
def light_process(isolated_config, monkeypatch):
    """gateway_process 去掉联网与 cron，只留收尾路径。"""
    monkeypatch.setattr("lumi.models.catalog.refresh", _noop)
    monkeypatch.setattr(bootstrap, "setup_cron", lambda *a, **k: 1 / 0)  # 不起 cron


async def test_shutdown_closes_detached_sessions(light_process):
    # detached 会话的 bridge 持非 daemon 的 aiosqlite 线程：不 aclose 进程退不掉
    session = _DetachedSession()
    registry.add("t-detached", session)

    async with bootstrap.gateway_process():
        pass

    assert session.closed
    assert registry.take("t-detached") is None


async def test_background_jobs_get_sigterm_before_sigkill(isolated_config, tmp_path):
    # 回归：先对全部后代 SIGKILL 兜底、再「优雅」关后台进程——TERM 收尾永远到不了
    from lumi.agents.runtime.bg_process import get_bg_manager
    from lumi.gateway.bridge import shutdown_shared_runtime

    marker = tmp_path / "graceful"
    ready = tmp_path / "ready"
    await get_bg_manager().start_task(
        f"trap 'sleep 1; touch {marker}; exit' TERM; touch {ready}; while :; do sleep 0.05; done",
        None,
        str(tmp_path),
    )
    while not ready.exists():
        await asyncio.sleep(0.02)
    await shutdown_shared_runtime()
    assert marker.exists()


async def test_lifespan_drains_before_channel_teardown(light_process, monkeypatch):
    # 回归：渠道会话池先被拆（在途轮串行等 5s 后被关库），drain 到最后才发
    from langgraph.runtime import RunControl

    from lumi.agents.core import run_control
    from lumi.gateway.channels.manager import manager
    from lumi.gateway.channels.ws import app, lifespan

    control = RunControl()  # 一条在跑的渠道轮
    run_control.register(control)
    seen: list[bool] = []

    async def stop_all() -> None:
        seen.append(control.drain_requested)
        run_control.unregister(control)

    monkeypatch.setattr(manager, "stop_all", stop_all)
    monkeypatch.setattr(bootstrap, "_DRAIN_GRACE_SECONDS", 0.05)
    async with lifespan(app):
        pass
    assert seen == [True]


async def test_catalog_refreshes_periodically(light_process, monkeypatch):
    # 回归：模型目录只在启动时拉一次，常驻 serve 此后再也拿不到新模型 / 新窗口
    calls: list[int] = []

    async def refresh() -> None:
        calls.append(1)

    monkeypatch.setattr("lumi.models.catalog.refresh", refresh)
    monkeypatch.setattr(bootstrap, "_CATALOG_REFRESH_SECONDS", 0.01)
    async with bootstrap.gateway_process():
        await asyncio.sleep(0.1)
    assert len(calls) >= 2

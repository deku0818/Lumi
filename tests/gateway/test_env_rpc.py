"""env_rpc 单测：状态查询 / 安装后台流 / 进度节流 / 防重复触发。"""

import asyncio
import threading

import pytest

from lumi.gateway import env_rpc, toolbox
from lumi.gateway.env_rpc import dispatch_env


class FakeDelivery:
    def __init__(self):
        self.events = []

    async def send_event(self, name, payload, match=None):
        self.events.append((name, payload))


class FakeHub:
    def __init__(self):
        self._d = FakeDelivery()

    @property
    def delivery(self):
        return self._d


@pytest.fixture
def fake_hub(monkeypatch):
    hub = FakeHub()
    monkeypatch.setattr(env_rpc, "hub", hub)
    env_rpc._installing = ""
    return hub


async def _wait_install_done():
    for _ in range(100):
        if not env_rpc._installing:
            await asyncio.sleep(0.02)  # 让收尾的 env.state 广播落地
            return
        await asyncio.sleep(0.02)
    raise TimeoutError("安装任务未结束")


async def test_env_status(fake_hub, monkeypatch):
    monkeypatch.setattr(toolbox, "status_all", lambda: {"tools": []})
    assert await dispatch_env("env_status", {}) == {"tools": [], "installing": ""}


async def test_env_install_rejects_unknown_target(fake_hub):
    with pytest.raises(ValueError, match="未知安装目标"):
        await dispatch_env("env_install", {"target": "ffmpeg"})


async def test_install_progress_throttled_and_state_broadcast(fake_hub, monkeypatch):
    def fake_install(name, progress=None):
        # 同一整数百分比重复回调应被节流为一条
        progress("下载 uv", 0.501)
        progress("下载 uv", 0.505)
        progress("下载 uv", 0.99)
        progress("安装 uv", None)

    monkeypatch.setattr(toolbox, "install", fake_install)
    monkeypatch.setattr(toolbox, "status_all", lambda: {"tools": ["x"]})
    assert await dispatch_env("env_install", {"target": "uv"}) == {"started": True}
    await _wait_install_done()

    progress = [p for n, p in fake_hub.delivery.events if n == "env.progress"]
    assert progress == [
        {"target": "uv", "phase": "下载 uv", "percent": 50},
        {"target": "uv", "phase": "下载 uv", "percent": 99},
        {"target": "uv", "phase": "安装 uv", "percent": -1},
    ]
    states = [p for n, p in fake_hub.delivery.events if n == "env.state"]
    # target 随 state 下发：多面板据此只响应与自己相关的安装
    assert states == [{"tools": ["x"], "target": "uv"}]


async def test_install_failure_reports_error_state(fake_hub, monkeypatch):
    def boom(name, progress=None):
        raise RuntimeError("网络不可达")

    monkeypatch.setattr(toolbox, "install", boom)
    monkeypatch.setattr(toolbox, "status_all", lambda: {"tools": []})
    await dispatch_env("env_install", {"target": "rg"})
    await _wait_install_done()
    name, state = fake_hub.delivery.events[-1]
    assert name == "env.state"
    assert state["error"] == {"target": "rg", "message": "网络不可达"}


async def test_install_duplicate_returns_not_started(fake_hub, monkeypatch):
    """安装全局互斥：target 之间有重叠（all ⊃ uv，lark-cli 内装 node），
    任一进行中都拒绝新安装——否则两线程并发写同一二进制。"""
    release = threading.Event()
    monkeypatch.setattr(toolbox, "install_missing", lambda p=None: release.wait(5))
    monkeypatch.setattr(toolbox, "status_all", lambda: {"tools": []})
    assert (await dispatch_env("env_install", {}))["started"] is True
    assert (await dispatch_env("env_install", {"target": "all"}))["started"] is False
    # 不同 target 也被拒：all 正在装 uv 时单独触发 uv 会并发写 bin_dir/uv
    assert (await dispatch_env("env_install", {"target": "uv"}))["started"] is False
    release.set()
    await _wait_install_done()

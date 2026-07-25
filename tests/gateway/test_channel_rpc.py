"""IM channel sidecar 存储 + RPC 测试（不起真实飞书连接：全程 enabled=False）。"""

from __future__ import annotations

import pytest

from lumi.gateway import channel_rpc
from lumi.gateway.channels import store
from lumi.utils.config import user_store


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    """把 lumi.json 重定向到临时文件，隔离 ~/.lumi（channel 数据落 "channels" 分区）。"""
    path = tmp_path / "lumi.json"
    monkeypatch.setattr(user_store, "CONFIG_FILE", path)
    return path


def test_store_roundtrip(sidecar):
    saved = store.save_feishu(
        {
            "enabled": False,
            "app_id": "cli_x",
            "tool_mode": "privileged",
            "allow_from": ["ou_a"],
        }
    )
    assert saved.app_id == "cli_x"
    assert saved.tool_mode == "privileged"
    assert sidecar.exists()
    loaded = store.load_feishu()
    assert loaded.app_id == "cli_x"
    assert loaded.allow_from == ["ou_a"]


def test_load_defaults_when_missing(sidecar):
    cfg = store.load_feishu()  # 文件不存在
    assert cfg.enabled is False
    assert cfg.allow_from == ["*"]  # 默认全开
    assert cfg.tool_mode == "auto"


def test_load_defaults_on_corrupt(sidecar):
    sidecar.write_text("{ not json", encoding="utf-8")
    cfg = store.load_feishu()
    assert cfg.enabled is False


async def test_rpc_get_channels_shape(sidecar):
    r = await channel_rpc.dispatch_channel("get_channels", {})
    ch = r["channels"][0]
    assert ch["name"] == "feishu"
    assert ch["enabled"] is False
    assert ch["status"]["state"] == "off"  # 未启用
    assert "config" in ch


async def test_rpc_save_persists_and_reflects(sidecar):
    r = await channel_rpc.dispatch_channel(
        "save_channel",
        {
            "name": "feishu",
            "config": {"enabled": False, "app_id": "cli_y", "group_policy": "open"},
        },
    )
    ch = r["channels"][0]
    assert ch["config"]["app_id"] == "cli_y"
    assert ch["config"]["group_policy"] == "open"
    assert sidecar.exists()


async def test_rpc_setup_diagnose_missing_creds(sidecar, monkeypatch):
    """凭证为空时远程侧不发网络请求直接四项 error；本地环境两项（cli/技能包）恒在清单前。

    local_env_checks 会真跑 lark-cli 子进程并读真实 ~/.lumi，必须 mock——
    否则结果随开发机装没装 lark-cli 漂移（违反测试密闭性约定）。
    """
    from dataclasses import asdict

    from lumi.gateway.channels.feishu import setup
    from lumi.gateway.channels.feishu.checks import Check

    fake_local = [
        asdict(
            Check(key="cli", name="lark-cli 未安装", tone="error", group="本地环境")
        ),
        asdict(Check(key="skills", name="飞书技能包", tone="error", group="本地环境")),
    ]
    monkeypatch.setattr(setup, "local_env_checks", lambda workspace: fake_local)
    r = await channel_rpc.dispatch_channel(
        "diagnose_feishu_setup",
        {"name": "feishu", "config": {"app_id": "", "app_secret": ""}},
    )
    assert [c["key"] for c in r["checks"]] == [
        "cli",
        "skills",
        "credentials",
        "scopes",
        "events",
        "version",
    ]
    local, remote = r["checks"][:2], r["checks"][2:]
    assert all(c["group"] == "本地环境" for c in local)
    assert all(c["group"] == "机器人接入" for c in remote)
    assert all(c["tone"] == "error" for c in remote)


async def test_rpc_unknown_channel_rejected(sidecar):
    with pytest.raises(ValueError):
        await channel_rpc.dispatch_channel(
            "save_channel", {"name": "wecom", "config": {}}
        )


# ── ChannelManager 生命周期（reload 只重启传输、会话池跨重连存活）──
class _FakeChannel:
    """替身：不连真飞书，只记录 start/stop。"""

    name = "feishu"
    instances: list = []

    def __init__(self, cfg, bridge_pool=None):
        self.config = cfg
        self.bridge_pool = bridge_pool
        self.stopped = False
        _FakeChannel.instances.append(self)

    async def start(self):
        pass

    async def stop(self):
        self.stopped = True

    def status(self):
        return {"state": "connected", "detail": ""}


@pytest.fixture
def fake_channel(monkeypatch):
    import lumi.gateway.channels.feishu as feishu_pkg

    _FakeChannel.instances = []
    monkeypatch.setattr(feishu_pkg, "FeishuChannel", _FakeChannel)
    return _FakeChannel


async def _reload_with(monkeypatch, cfg):
    from lumi.gateway.channels import manager as mgr_mod

    monkeypatch.setattr(mgr_mod, "load_feishu", lambda: cfg)


async def test_manager_reuses_pool_across_same_workspace_reload(
    monkeypatch, fake_channel
):
    from lumi.gateway.channels.config import FeishuChannelConfig
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    cfg = FeishuChannelConfig(enabled=True, app_id="x", app_secret="y", workspace="/w")
    await _reload_with(monkeypatch, cfg)

    await m.reload()
    pool1 = m._pools["feishu"]
    ch1 = m._channels["feishu"]

    await m.reload()  # 同 workspace 再 reload：会话池复用、旧传输停一次
    assert m._pools["feishu"] is pool1  # 进行中的会话不被清空
    assert ch1.stopped is True
    assert m._channels["feishu"] is not ch1
    await m.stop_all()


async def test_manager_new_pool_on_workspace_change(monkeypatch, fake_channel):
    from lumi.gateway.channels.config import FeishuChannelConfig
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    await _reload_with(
        monkeypatch,
        FeishuChannelConfig(enabled=True, app_id="x", app_secret="y", workspace="/w"),
    )
    await m.reload()
    pool1 = m._pools["feishu"]

    await _reload_with(
        monkeypatch,
        FeishuChannelConfig(enabled=True, app_id="x", app_secret="y", workspace="/w2"),
    )
    await m.reload()  # workspace 变 → 换池
    assert m._pools["feishu"] is not pool1
    await m.stop_all()


async def test_manager_disable_drops_pool(monkeypatch, fake_channel):
    from lumi.gateway.channels.config import FeishuChannelConfig
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    await _reload_with(
        monkeypatch, FeishuChannelConfig(enabled=True, app_id="x", app_secret="y")
    )
    await m.reload()
    assert "feishu" in m._pools

    await _reload_with(monkeypatch, FeishuChannelConfig(enabled=False))
    await m.reload()  # 禁用 → 连会话池一并回收
    assert "feishu" not in m._pools
    assert "feishu" not in m._channels


async def test_manager_concurrent_reload_serialized(monkeypatch, fake_channel):
    """并发 reload 经 _reload_lock 串行化，不会建出重复 channel/孤儿传输。"""
    import asyncio

    from lumi.gateway.channels.config import FeishuChannelConfig
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    await _reload_with(
        monkeypatch, FeishuChannelConfig(enabled=True, app_id="x", app_secret="y")
    )

    await asyncio.gather(m.reload(), m.reload(), m.reload())
    # 仅一个 channel 存活；之前建的都被 stop（无孤儿未停传输）
    alive = m._channels["feishu"]
    not_alive = [c for c in fake_channel.instances if c is not alive]
    assert all(c.stopped for c in not_alive)
    await m.stop_all()

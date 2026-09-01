"""IM channel sidecar 存储 + RPC 测试（不起真实飞书连接：全程 enabled=False）。"""

from __future__ import annotations

import pytest

from lumi.gateway import channel_rpc
from lumi.gateway.channels import store
from lumi.gateway.channels.feishu.lark_profile import sync_profile as _real_sync_profile
from lumi.utils.config import user_store


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    """把 lumi.json 重定向到临时文件，隔离 ~/.lumi（channel 数据落 "channels" 分区）。"""
    path = tmp_path / "lumi.json"
    monkeypatch.setattr(user_store, "CONFIG_FILE", path)
    return path


@pytest.fixture(autouse=True)
def no_lark_cli(monkeypatch):
    """RPC 保存/删除会 best-effort 同步 lark-cli profile——测试不真 spawn lark-cli。"""
    monkeypatch.setattr(
        channel_rpc.lark_profile, "sync_profile", lambda cfg: ("", "测试跳过")
    )
    monkeypatch.setattr(
        channel_rpc.lark_profile,
        "remove_profile",
        lambda bot_id, cli_profile: None,
    )


def test_store_roundtrip(sidecar):
    saved = store.save_feishu_bot(
        {
            "enabled": False,
            "app_id": "cli_x",
            "tool_mode": "privileged",
            "allow_from": ["ou_a"],
        }
    )
    assert saved.app_id == "cli_x"
    assert saved.tool_mode == "privileged"
    assert saved.id  # 缺 id 自动生成
    assert sidecar.exists()
    bots = store.load_feishu_bots()
    assert len(bots) == 1
    assert bots[0].app_id == "cli_x"
    assert bots[0].allow_from == ["ou_a"]


def test_load_empty_when_missing(sidecar):
    assert store.load_feishu_bots() == []  # 文件不存在


def test_load_empty_on_corrupt(sidecar):
    sidecar.write_text("{ not json", encoding="utf-8")
    assert store.load_feishu_bots() == []


def test_legacy_single_config_migrates(sidecar):
    """旧版单对象格式读取时就地迁移成列表，id 确定性、legacy_threads 保住老会话。"""
    user_store.write_section(
        "channels",
        {"feishu": {"enabled": False, "app_id": "cli_old", "workspace": "/w"}},
    )
    bots = store.load_feishu_bots()
    assert len(bots) == 1
    bot = bots[0]
    assert bot.app_id == "cli_old"
    assert bot.legacy_threads is True
    assert bot.thread_prefix == "feishu-"  # 老会话 key 不变
    # 读路径纯读不落盘（shell spawn 热路径也会走到），id 确定性派生不随读取漂移
    assert store.load_feishu_bots()[0].id == bot.id
    raw = user_store.read_section("channels", {})["feishu"]
    assert isinstance(raw, dict)
    # 首次保存顺带把迁移落盘成列表
    store.save_feishu_bot({**bot.model_dump(), "name": "改名"})
    raw = user_store.read_section("channels", {})["feishu"]
    assert isinstance(raw, list)
    assert raw[0]["id"] == bot.id and raw[0]["legacy_threads"] is True


def test_new_bot_thread_prefix_carries_id(sidecar):
    saved = store.save_feishu_bot({"app_id": "cli_x"})
    assert saved.legacy_threads is False
    assert saved.thread_prefix == f"feishu-{saved.id}-"
    assert saved.cli_profile == ""  # 未同步：由 sync_profile 解析后写回


def test_save_upsert_by_id(sidecar):
    a = store.save_feishu_bot({"app_id": "cli_a", "name": "A"})
    store.save_feishu_bot({"id": a.id, "app_id": "cli_a", "name": "A2"})
    bots = store.load_feishu_bots()
    assert len(bots) == 1
    assert bots[0].name == "A2"


def test_save_rejects_duplicate_workspace(sidecar):
    store.save_feishu_bot({"app_id": "cli_a", "workspace": "/w", "name": "A"})
    with pytest.raises(ValueError, match="只能配一个机器人"):
        store.save_feishu_bot({"app_id": "cli_b", "workspace": "/w"})


def test_save_rejects_duplicate_app_id(sidecar):
    store.save_feishu_bot({"app_id": "cli_a", "name": "A"})
    with pytest.raises(ValueError, match="同一应用不能配两条"):
        store.save_feishu_bot({"app_id": "cli_a", "workspace": "/other"})


def test_save_enabled_requires_workspace(sidecar):
    with pytest.raises(ValueError, match="绑定项目"):
        store.save_feishu_bot({"enabled": True, "app_id": "cli_a"})


def test_delete_bot(sidecar):
    a = store.save_feishu_bot({"app_id": "cli_a"})
    removed = store.delete_feishu_bot(a.id)
    assert removed is not None and removed["id"] == a.id
    assert store.load_feishu_bots() == []
    assert store.delete_feishu_bot("nope") is None


def test_shell_env_for_matches_bot_workspace(sidecar, tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    bot = store.save_feishu_bot({"app_id": "cli_a", "workspace": str(ws)})
    # 未同步 profile（cli_profile 空）：不注入，回落全局身份
    assert store.shell_env_for(str(ws)) == {}
    store.save_feishu_bot({**bot.model_dump(), "cli_profile": "lumi-abc"})
    assert store.shell_env_for(str(ws)) == {"LARKSUITE_CLI_PROFILE": "lumi-abc"}
    assert store.shell_env_for(str(tmp_path / "other")) == {}
    assert store.shell_env_for("") == {}


def test_shell_env_for_matches_subdirectory(sidecar, tmp_path):
    """项目子目录同样命中——后台任务以 shell 当前 cwd spawn，cd 进子目录不该丢身份。"""
    ws = tmp_path / "proj"
    sub = ws / "docs" / "deep"
    sub.mkdir(parents=True)
    store.save_feishu_bot(
        {"app_id": "cli_a", "workspace": str(ws), "cli_profile": "lumi-abc"}
    )
    assert store.shell_env_for(str(sub)) == {"LARKSUITE_CLI_PROFILE": "lumi-abc"}
    # 兄弟目录不命中（前缀相似但不是子路径）
    other = tmp_path / "proj2"
    other.mkdir()
    assert store.shell_env_for(str(other)) == {}


def test_invalid_entry_neither_squats_nor_blocks_delete(sidecar):
    """校验不过的坏条目：不占用项目/app_id（否则不可见又删不掉，锁死用户），
    但按 id 删除仍能删掉并返回原始字段供回收 profile。"""
    user_store.write_section(
        "channels",
        {
            "feishu": [
                {
                    "id": "bad1",
                    "app_id": "cli_bad",
                    "workspace": "/w2",
                    "cli_profile": "lumi-bad1",
                    "allow_from": "not-a-list",  # 类型错 → model_validate 失败
                }
            ]
        },
    )
    assert store.load_feishu_bots() == []  # 坏条目不可见
    # 不占用：同项目/同 app 都能新建
    store.save_feishu_bot({"app_id": "cli_bad", "workspace": "/w2"})
    # 坏条目仍可按 id 删除，返回 raw dict（含回收 profile 所需字段）
    removed = store.delete_feishu_bot("bad1")
    assert removed is not None
    assert removed["cli_profile"] == "lumi-bad1"


def test_sync_profile_reuses_own_named_profile(sidecar, monkeypatch):
    """cli_profile 记录丢失但自建 lumi-{id} 还在：必须复用而非 profile add 撞重名。

    经 _real_sync_profile 调（模块导入时捕获）——autouse 的 no_lark_cli 把
    lark_profile.sync_profile 换成了假的。
    """
    from lumi.gateway.channels.feishu import lark_profile

    monkeypatch.setattr(
        lark_profile,
        "_list_profiles",
        lambda: [{"name": "lumi-b1", "appId": "cli_x"}],
    )
    cfg = store.save_feishu_bot({"id": "b1", "app_id": "cli_x", "app_secret": "s"})
    assert cfg.cli_profile == ""
    profile, error = _real_sync_profile(cfg)
    assert (profile, error) == ("lumi-b1", "")


async def test_rpc_get_channels_shape(sidecar):
    r = await channel_rpc.dispatch_channel("get_channels", {})
    assert r["channels"] == []  # 无机器人 → 空列表
    store.save_feishu_bot({"app_id": "cli_x"})
    r = await channel_rpc.dispatch_channel("get_channels", {})
    ch = r["channels"][0]
    assert ch["name"] == "feishu"
    assert ch["enabled"] is False
    assert ch["status"]["state"] == "off"  # 未启用
    assert ch["config"]["id"]


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


async def test_rpc_delete_channel(sidecar):
    r = await channel_rpc.dispatch_channel(
        "save_channel", {"name": "feishu", "config": {"app_id": "cli_y"}}
    )
    bot_id = r["channels"][0]["config"]["id"]
    r = await channel_rpc.dispatch_channel(
        "delete_channel", {"name": "feishu", "bot_id": bot_id}
    )
    assert r["channels"] == []


async def test_rpc_setup_diagnose_missing_creds(sidecar, monkeypatch):
    """凭证为空时远程侧不发网络请求直接四项 error；本地环境组恒在清单前。

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
    monkeypatch.setattr(setup, "local_env_checks", lambda *args: fake_local)
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


# ── ChannelManager 生命周期（reload 按机器人 diff、会话池跨重连存活）──
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


def _bot(**kw) -> object:
    from lumi.gateway.channels.config import FeishuChannelConfig

    return FeishuChannelConfig(
        **{"id": "b1", "app_id": "x", "app_secret": "y", "workspace": "/w", **kw}
    )


async def _reload_with(monkeypatch, bots: list):
    from lumi.gateway.channels import manager as mgr_mod

    monkeypatch.setattr(mgr_mod, "load_feishu_bots", lambda: bots)


async def test_manager_reuses_pool_across_same_workspace_reload(
    monkeypatch, fake_channel
):
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    await _reload_with(monkeypatch, [_bot(enabled=True)])

    await m.reload()
    pool1 = m._pools["b1"]
    ch1 = m._channels["b1"]

    # 配置变更（凭证换了）再 reload：会话池复用、旧传输停一次
    await _reload_with(monkeypatch, [_bot(enabled=True, app_id="x2")])
    await m.reload()
    assert m._pools["b1"] is pool1  # 进行中的会话不被清空
    assert ch1.stopped is True
    assert m._channels["b1"] is not ch1
    await m.stop_all()


async def test_manager_skips_unchanged_bot(monkeypatch, fake_channel):
    """按机器人 diff：改 A 不弹 B 的连接（多机器人的意义所在）。"""
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    a = _bot(id="a", enabled=True, app_id="xa", workspace="/wa")
    b = _bot(id="b", enabled=True, app_id="xb", workspace="/wb")
    await _reload_with(monkeypatch, [a, b])
    await m.reload()
    ch_b = m._channels["b"]

    a2 = _bot(id="a", enabled=True, app_id="xa2", workspace="/wa")
    await _reload_with(monkeypatch, [a2, b])
    await m.reload()
    assert m._channels["b"] is ch_b  # B 没变 → 原地不动
    assert ch_b.stopped is False
    assert m._channels["a"].config.app_id == "xa2"
    await m.stop_all()


async def test_manager_restarts_error_bot_on_unchanged_reload(
    monkeypatch, fake_channel
):
    """start() 失败的机器人不算「在跑」：配置没变的「保存并重连」必须真的重连，
    这是用户唯一的不重启恢复手段。"""
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    bots = [_bot(enabled=True)]
    await _reload_with(monkeypatch, bots)
    await m.reload()
    dead = m._channels["b1"]
    dead.status = lambda: {"state": "error", "detail": "启动失败"}

    await m.reload()  # 配置未变
    assert m._channels["b1"] is not dead  # error 态 → 重建
    assert dead.stopped is True
    await m.stop_all()


async def test_manager_new_pool_on_workspace_change(monkeypatch, fake_channel):
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    await _reload_with(monkeypatch, [_bot(enabled=True)])
    await m.reload()
    pool1 = m._pools["b1"]

    await _reload_with(monkeypatch, [_bot(enabled=True, workspace="/w2")])
    await m.reload()  # workspace 变 → 换池
    assert m._pools["b1"] is not pool1
    await m.stop_all()


async def test_manager_disable_drops_pool(monkeypatch, fake_channel):
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    await _reload_with(monkeypatch, [_bot(enabled=True)])
    await m.reload()
    assert "b1" in m._pools

    await _reload_with(monkeypatch, [_bot(enabled=False)])
    await m.reload()  # 禁用 → 连会话池一并回收
    assert "b1" not in m._pools
    assert "b1" not in m._channels


async def test_manager_delete_drops_everything(monkeypatch, fake_channel):
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    await _reload_with(monkeypatch, [_bot(enabled=True)])
    await m.reload()

    await _reload_with(monkeypatch, [])  # 机器人被删除
    await m.reload()
    assert m._pools == {} and m._channels == {}


async def test_manager_concurrent_reload_serialized(monkeypatch, fake_channel):
    """并发 reload 经 _reload_lock 串行化，不会建出重复 channel/孤儿传输。"""
    import asyncio

    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    await _reload_with(monkeypatch, [_bot(enabled=True)])

    await asyncio.gather(m.reload(), m.reload(), m.reload())
    # 仅一个 channel 存活；之前建的都被 stop（无孤儿未停传输）
    alive = m._channels["b1"]
    not_alive = [c for c in fake_channel.instances if c is not alive]
    assert all(c.stopped for c in not_alive)
    await m.stop_all()


async def test_watch_store_hot_reloads_cli_write(sidecar, fake_channel):
    """进程外写入（`lumi feishu config`）没有 RPC 通道，watch_store 兜底热生效。"""
    import asyncio

    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    saved = store.save_feishu_bot(
        {"enabled": True, "app_id": "x", "app_secret": "y", "workspace": "/w"}
    )
    await m.reload()
    assert len(fake_channel.instances) == 1

    watcher = asyncio.create_task(m.watch_store(interval=0.01))
    store.save_feishu_bot(
        {
            "id": saved.id,
            "enabled": True,
            "app_id": "x2",
            "app_secret": "y",
            "workspace": "/w",
        }
    )
    await asyncio.sleep(0.1)
    watcher.cancel()
    assert fake_channel.instances[-1].config.app_id == "x2"
    await m.stop_all()


async def test_watch_store_survives_transient_error(sidecar, fake_channel, monkeypatch):
    """单轮失败不杀监视器、不吞变更：下轮对同一次 mtime 变更重试成功。"""
    import asyncio

    from lumi.gateway.channels import manager as mgr_mod
    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    saved = store.save_feishu_bot(
        {"enabled": True, "app_id": "x", "app_secret": "y", "workspace": "/w"}
    )
    await m.reload()

    real_load = mgr_mod.load_feishu_bots
    calls = {"n": 0}

    def flaky_load():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return real_load()

    monkeypatch.setattr(mgr_mod, "load_feishu_bots", flaky_load)
    watcher = asyncio.create_task(m.watch_store(interval=0.01))
    store.save_feishu_bot(
        {
            "id": saved.id,
            "enabled": True,
            "app_id": "x2",
            "app_secret": "y",
            "workspace": "/w",
        }
    )
    await asyncio.sleep(0.15)
    watcher.cancel()
    assert calls["n"] >= 2  # 第一轮抛错后仍在跑
    assert fake_channel.instances[-1].config.app_id == "x2"  # 变更没被吞
    await m.stop_all()


async def test_watch_store_ignores_unrelated_section_write(sidecar, fake_channel):
    """lumi.json 其他分区的写入只动 mtime 不动 channels——不该弹飞书长连接。"""
    import asyncio

    from lumi.gateway.channels.manager import ChannelManager

    m = ChannelManager()
    store.save_feishu_bot(
        {"enabled": True, "app_id": "x", "app_secret": "y", "workspace": "/w"}
    )
    await m.reload()

    watcher = asyncio.create_task(m.watch_store(interval=0.01))
    user_store.write_section("providers", {"acme": {}})
    await asyncio.sleep(0.1)
    watcher.cancel()
    assert len(fake_channel.instances) == 1  # 未重启传输
    await m.stop_all()

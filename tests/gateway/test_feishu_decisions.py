"""飞书渠道经决策落地的行为回归测试。

- 每日整理按墙钟目标分段睡：合盖 / 调钟后不在错的时刻触发，错过即醒来补跑；
- lark-cli 一律装进 Lumi 自有 npm prefix（免 sudo、不碰系统全局目录）；有 Node
  缺 npm 时不把人送去环境页（那里 Node 显示已装，死路）；
- 回复某条消息并 @机器人：父消息正文以引用块进模型侧，不进气泡；
- 机器人专属 lark-cli profile 缺失时不回落全局身份（妙记体检 / 订阅）。
外部依赖（lark-cli / npm / 飞书开放平台）一律打桩，lumi 侧走真实代码。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from lumi.gateway import toolbox
from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.gateway.channels.feishu import channel as channel_mod
from lumi.gateway.channels.feishu import daily_dream, minutes, setup
from lumi.gateway.channels.feishu import inbound as inb
from lumi.gateway.channels.feishu.channel import FeishuChannel
from lumi.utils.config import get_config

# ── 每日整理：墙钟目标分段睡 ─────────────────────────────────────────────


async def _run_dream_loop_at(monkeypatch, jumps: list[timedelta]) -> datetime:
    """从 20:00 起跑 daily_dream_loop（目标 03:00），返回周期实际触发的墙钟时刻。

    ``jumps[i]`` = 第 i 次 sleep 期间墙钟额外跳过的时长（合盖挂起 / 调钟）——单调
    时钟在挂起时不走，asyncio.sleep 醒来时墙钟已远超预期。
    """
    clock = SimpleNamespace(now=datetime(2026, 1, 1, 20, 0))

    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now

    async def fake_sleep(seconds):
        clock.now += timedelta(seconds=seconds) + (
            jumps.pop(0) if jumps else timedelta()
        )

    fired: list[datetime] = []

    async def fake_cycle(pool, config, channel_name):
        fired.append(clock.now)
        raise asyncio.CancelledError  # 只看第一次触发

    monkeypatch.setattr(daily_dream, "datetime", _FakeDateTime)
    monkeypatch.setattr(
        daily_dream,
        "asyncio",
        SimpleNamespace(sleep=fake_sleep, CancelledError=asyncio.CancelledError),
    )
    monkeypatch.setattr(daily_dream, "_run_cycle", fake_cycle)
    cfg = SimpleNamespace(daily_dream_enabled=True, daily_dream_time="03:00")
    with pytest.raises(asyncio.CancelledError):
        await daily_dream.daily_dream_loop(None, cfg, "feishu")
    return fired[0]


async def test_daily_dream_catches_up_right_after_wake(monkeypatch):
    """合盖 20:00→08:00：醒来 5 分钟内补跑，而不是再睡满 7 小时到 15:00。"""
    fired = await _run_dream_loop_at(monkeypatch, [timedelta(hours=12)])
    assert datetime(2026, 1, 2, 8, 0) <= fired <= datetime(2026, 1, 2, 8, 5)


@pytest.mark.parametrize("jump", [timedelta(hours=3), timedelta(hours=-2)])
async def test_daily_dream_fires_at_wall_clock_after_clock_change(monkeypatch, jump):
    """睡眠期间调钟（前拨 / 回拨）：仍在墙钟 03:00 触发，不按旧时长漂到别的钟点。"""
    fired = await _run_dream_loop_at(monkeypatch, [jump])
    assert datetime(2026, 1, 2, 3, 0) <= fired < datetime(2026, 1, 2, 3, 5)


# ── lark-cli 装进 Lumi 自有 npm prefix ───────────────────────────────────


@pytest.fixture
def clean_path(isolated_config, tmp_path, monkeypatch):
    """干净 PATH：只含一个可控的「系统 bin」目录。"""
    system_bin = tmp_path / "system-bin"
    system_bin.mkdir()
    monkeypatch.setenv("PATH", str(system_bin))
    return system_bin


def _fake_exe(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f'#!/bin/sh\necho "{name} 1.0.0"\n')
    path.chmod(0o755)
    return path


def test_install_lark_cli_into_lumi_prefix(clean_path, monkeypatch):
    """npm -g 装到工具箱自有 prefix（系统全局目录属 root 时 EACCES），再链进 bin_dir。"""
    _fake_exe(clean_path, "npm")
    prefix = get_config().toolbox_dir / "npm-global"
    calls: list[list[str]] = []

    def fake_run(cmd, timeout=30):
        if cmd[1:] == ["--version"]:
            return True, "1.0.0"
        calls.append(cmd[1:])
        _fake_exe(prefix / "bin", "lark-cli")  # npm 按 prefix 布局落产物
        return True, ""

    monkeypatch.setattr(toolbox, "_run", fake_run)
    status = toolbox.install_lark_cli()

    assert calls == [["install", "-g", "--prefix", str(prefix), "@larksuite/cli"]]
    assert status.source == "toolbox"
    link = get_config().bin_dir / "lark-cli"
    assert link.resolve() == (prefix / "bin" / "lark-cli").resolve()


def test_install_lark_cli_node_without_npm_says_install_npm(clean_path):
    """有系统 Node 缺 npm：直说去用系统包管理器装 npm——环境页显示 Node 已装，是死路。"""
    _fake_exe(clean_path, "node")
    with pytest.raises(RuntimeError, match="系统 Node 缺少 npm"):
        toolbox.install_lark_cli()


def test_local_env_checks_node_without_npm_no_env_nav(clean_path):
    """体检同理：仅 Node 也缺时才把人送去环境页；有 Node 缺 npm 不给 fix_nav。"""
    checks = setup.local_env_checks("")
    assert checks[0]["fix_nav"] == "env"  # Node 都没有：环境页一键装

    _fake_exe(clean_path, "node")
    checks = setup.local_env_checks("")
    assert checks[0]["fix_nav"] == "" and checks[0]["fix_action"] == ""
    assert "系统 Node 缺少 npm" in checks[0]["detail"]


def test_lark_cli_fix_cmds_follow_where_it_is_installed(clean_path, monkeypatch):
    """升级命令在装的地方升：Lumi 自装的走同一 prefix，系统全局装的走 npm -g（在
    Lumi prefix 里 update 一个系统装的 cli 什么也不做）。缺 cli 只给一键安装——手敲装进
    prefix 的不会被链进 bin 目录，体检照样判缺。"""
    prefix = str(get_config().toolbox_dir / "npm-global")
    checks = minutes.diagnose("cli_x", "lumi-x")  # lark-cli 未装
    assert checks[0]["fix_cmd"] == "" and checks[0]["fix_action"] == "lark-cli"

    monkeypatch.setattr(toolbox, "lark_skill_versions", lambda path: {})
    for source, expect in (
        ("toolbox", f'npm update -g --prefix "{prefix}" @larksuite/cli'),
        ("system", "npm update -g @larksuite/cli"),
    ):
        monkeypatch.setattr(
            toolbox,
            "detect",
            lambda name, s=source: toolbox.ToolStatus(name, s, "1.0.99", "/x/lark-cli"),
        )
        checks = setup.local_env_checks("/some/project", "bot1", "lumi-x", "cli_x")
        skills = next(c for c in checks if c["key"] == "skills")
        assert skills["fix_cmd"].endswith(expect)


# ── 回复并 @机器人：父消息正文只进模型侧 ─────────────────────────────────


def _reply_event(parent_id: str = "om_parent"):
    return SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id="om_child",
                chat_id="oc_dm",
                chat_type="p2p",
                message_type="text",
                content=json.dumps({"text": "帮我总结"}),
                mentions=None,
                parent_id=parent_id,
                create_time=1000,
            ),
            sender=SimpleNamespace(
                sender_type="user", sender_id=SimpleNamespace(open_id="ou_me")
            ),
        )
    )


def _parent(msg_type: str, content: dict, sender_id: str, mentions=None):
    """lark ``Message`` 的最小形态（字段名与 im.v1.message.get 返回对齐）。"""
    return SimpleNamespace(
        message_id="om_parent",
        msg_type=msg_type,
        body=SimpleNamespace(content=json.dumps(content)),
        mentions=mentions,
        sender=SimpleNamespace(
            id=sender_id,
            id_type="app_id" if sender_id.startswith("cli_") else "open_id",
        ),
    )


async def _admitted(monkeypatch, parent) -> inb._Pending:
    """跑真实 on_message（父消息打桩），返回交给 _admit 的那条 _Pending。"""
    ch = FeishuChannel(FeishuChannelConfig(app_id="cli_self"))
    fi = ch.inbound
    admitted: list[inb._Pending] = []

    async def fake_admit(chat_id, thread_id, pending, env=""):
        admitted.append(pending)

    async def fake_resolve(chat, ids):
        return {"ou_me": "张三"}

    monkeypatch.setattr(fi, "_fetch_parent_sync", lambda pid: parent)
    monkeypatch.setattr(fi, "_admit", fake_admit)
    monkeypatch.setattr(fi, "_sync_session_title", lambda *a, **k: None)
    monkeypatch.setattr(ch.directory, "resolve_senders_in_chat", fake_resolve)
    await fi.on_message(_reply_event())
    assert admitted, "on_message 未跑到 _admit（异常被 try/except 吞了）"
    return admitted[0]


async def test_reply_quotes_parent_text_for_model_not_bubble(monkeypatch):
    parent = _parent(
        "text",
        {"text": "周报 @_user_1 负责\n周五前交"},
        "ou_other",
        mentions=[SimpleNamespace(key="@_user_1", name="李雷")],
    )
    pending = await _admitted(monkeypatch, parent)

    captured: dict = {}

    async def fake_run_turn(ch, bridge, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(inb, "run_turn", fake_run_turn)
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    await fi._run_batch(fi.channel, None, "oc_dm", "t", [pending])

    # 模型侧：引用块在正文之前，@ 占位已换成姓名
    assert captured["content"] == (
        "<sender>张三</sender>\n> 周报 @李雷 负责\n> 周五前交\n帮我总结"
    )
    # 气泡侧：只显示用户自己发的话
    assert captured["message_meta"]["items"][0]["text"] == "帮我总结"


async def test_reply_quotes_parent_in_relay_prompt(monkeypatch):
    """直连模式同样带上引用：cc 也需要知道「总结」的是哪段话。"""
    parent = _parent("text", {"text": "被回复的话"}, "ou_other")
    pending = await _admitted(monkeypatch, parent)

    prompts: list[str] = []

    async def fake_relay(ch, **kwargs):
        prompts.append(kwargs["prompt"])

    monkeypatch.setattr(inb, "run_relay_turn", fake_relay)
    fi = FeishuChannel(FeishuChannelConfig()).inbound
    await fi._run_relay_batch(fi.channel, "oc_dm", "t", [pending])
    assert prompts == ["> 被回复的话\n帮我总结"]


@pytest.mark.parametrize(
    "msg_type,content,expect",
    [
        (
            "post",
            {"zh_cn": {"content": [[{"tag": "text", "text": "富文本正文"}]]}},
            "富文本正文",
        ),
        (
            "interactive",
            {
                "user_dsl": json.dumps(
                    {"body": {"elements": [{"tag": "markdown", "content": "卡片正文"}]}}
                )
            },
            "卡片正文",
        ),
    ],
)
async def test_reply_quote_extracts_by_parent_type(
    monkeypatch, msg_type, content, expect
):
    pending = await _admitted(monkeypatch, _parent(msg_type, content, "cli_other_bot"))
    assert pending.quote == expect
    assert pending.text == "帮我总结"


async def test_reply_to_own_bot_message_not_quoted(monkeypatch):
    """父消息是本机器人自己发的：已在会话历史里，不重复引用。"""
    parent = _parent("text", {"text": "我之前的回答"}, "cli_self")
    pending = await _admitted(monkeypatch, parent)
    assert pending.quote == ""


# ── 严格按机器人专属 profile：缺失不回落全局身份 ─────────────────────────


def test_minutes_diagnose_blocks_without_profile(monkeypatch):
    """profile 未同步：不去问全局 active profile（结论会记错在本机器人名下）。"""
    monkeypatch.setattr(
        toolbox,
        "detect",
        lambda name: toolbox.ToolStatus(name, "system", "", "/x/lark-cli"),
    )

    def boom(profile=""):
        raise AssertionError("不应在无 profile 时查询授权状态")

    monkeypatch.setattr(minutes, "_auth_status", boom)
    checks = minutes.diagnose("cli_x", "")
    assert [c["key"] for c in checks] == ["cli", "auth", "scope", "subscription"]
    assert checks[0]["tone"] == "ok"
    assert all(c["tone"] == "error" for c in checks[1:])
    assert "保存机器人" in checks[1]["detail"]


async def test_ensure_subscription_skipped_without_profile(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(channel_mod, "ensure_subscription", called.append)
    ch = FeishuChannel(FeishuChannelConfig(cli_profile=""))
    await ch._ensure_subscription()
    assert called == []

    ch = FeishuChannel(FeishuChannelConfig(cli_profile="lumi-x"))
    monkeypatch.setattr(
        channel_mod, "ensure_subscription", lambda p: called.append(p) or ""
    )
    await ch._ensure_subscription()
    assert called == ["lumi-x"]

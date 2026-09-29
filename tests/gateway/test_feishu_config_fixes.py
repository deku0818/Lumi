"""飞书渠道配置 / 身份 / 体检的回归测试。

每个用例对应一个曾经的缺陷：启动期取不到 bot_open_id 永久卡死、失效 profile 被
「跳过同步」固化、妙记取数命令改掉会话 shell 的 cwd、未设置的 ${ENV} 引用绕过
空值守卫、workspace 写法差异绕过「一项目一机器人」、技能清单为空被当成「待安装」。
外部依赖（lark-cli / 飞书开放平台）一律打桩，lumi 侧走真实代码。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shlex
import stat
import subprocess
import sys

import pytest

from lumi.gateway import toolbox
from lumi.gateway.channels import store
from lumi.gateway.channels.config import FeishuChannelConfig, resolve_ref
from lumi.gateway.channels.feishu import channel as channel_mod
from lumi.gateway.channels.feishu import lark_profile, minutes, setup
from lumi.gateway.channels.feishu.channel import FeishuChannel

# ── 公共桩 ───────────────────────────────────────────────────────────────


def _fake_channels_store(monkeypatch, bots: list[dict]) -> dict:
    """把 lumi.json 的 channels 分区换成内存 dict（读写都走真实 store 逻辑）。"""
    state: dict = {"feishu": copy.deepcopy(bots)}
    monkeypatch.setattr(store, "_read", lambda: copy.deepcopy(state))

    def write(key: str, value: dict) -> None:
        state.clear()
        state.update(copy.deepcopy(value))

    monkeypatch.setattr(store.user_store, "write_section", write)
    return state


def _fake_lark_cli(monkeypatch, profiles: list[dict] | None, add_ok: bool = True):
    """lark-cli 桩：profiles=None 表示 CLI 不可用；返回调用记录。"""
    calls: list[tuple[str, ...]] = []

    def run_cli(*args: str, profile: str = "", input_text: str | None = None):
        calls.append(args)
        if profiles is None:
            return -1, "lark-cli 不在 PATH"
        if args[:2] == ("profile", "list"):
            return 0, json.dumps(profiles)
        if args[:2] == ("profile", "add"):
            if not add_ok:
                return 1, "add: network error"
            name = args[args.index("--name") + 1]
            profiles.append({"name": name, "appId": args[args.index("--app-id") + 1]})
            return 0, ""
        if args[:2] == ("profile", "remove"):
            profiles[:] = [p for p in profiles if p["name"] != args[2]]
            return 0, ""
        raise AssertionError(f"unexpected lark-cli call {args}")

    monkeypatch.setattr(lark_profile, "run_cli", run_cli)
    return calls


def _quiet_channel(monkeypatch, cfg: FeishuChannelConfig, fetch) -> FeishuChannel:
    """真实 start() 流程，只把连网部分（WS 线程 / bot info / 后台循环）换成桩。"""

    async def noop(*a, **k) -> None:
        return None

    monkeypatch.setattr(channel_mod, "daily_dream_loop", noop)
    ch = FeishuChannel(cfg)
    ch._run_ws_in_thread = lambda: None
    ch._fetch_bot_open_id = fetch
    ch.inbound.notification_loop = noop
    ch._directory.warmup = noop
    ch.streaming.start_cleanup = lambda: None
    return ch


# ── 5. bot_open_id 启动期失败后自愈 ─────────────────────────────────────────


async def test_bot_open_id_retried_after_startup_failure(monkeypatch, tmp_path):
    """启动那一刻断网拿不到 bot_open_id，网络恢复后必须补上。

    回归：只在启动时取一次，失败即永久 None → 状态灯卡「连接中」、群 @ 全部被丢。
    """
    monkeypatch.setattr(channel_mod, "_BOT_ID_RETRY_S", 0.0)
    answers = iter([None, "ou_bot"])
    ch = _quiet_channel(
        monkeypatch,
        FeishuChannelConfig(app_id="cli_x", app_secret="s", workspace=str(tmp_path)),
        lambda: next(answers, "ou_bot"),
    )
    task = asyncio.create_task(ch.start())
    try:
        async with asyncio.timeout(5):
            while ch.bot_open_id != "ou_bot":
                await asyncio.sleep(0.05)
        ch._ws_client._conn = object()  # WS 已连上
        assert ch.status()["state"] == "connected"
    finally:
        ch._running = False
        task.cancel()


# ── 6. save_bot_synced 不固化失效 profile ─────────────────────────────────


def _bot(**kw) -> dict:
    return {"id": "b1", "app_id": "cli_a", "app_secret": "s", **kw}


def test_toggle_save_resyncs_missing_profile(monkeypatch):
    """记录的 profile 已被外部删除：纯开关保存也得重新同步（复用现成同 app 的）。"""
    _fake_channels_store(monkeypatch, [_bot(cli_profile="lumi-b1")])
    _fake_lark_cli(monkeypatch, [{"name": "mine", "appId": "cli_a"}])
    cfg, _ = lark_profile.save_bot_synced(_bot(cli_profile="lumi-b1", enabled=False))
    assert cfg.cli_profile == "mine"


def test_toggle_save_drops_mismatched_profile_when_sync_fails(monkeypatch):
    """记录的 profile 指向别的 app 且同步失败：清空（回落全局）好过绑错身份。"""
    _fake_channels_store(monkeypatch, [_bot(cli_profile="other")])
    _fake_lark_cli(monkeypatch, [{"name": "other", "appId": "cli_z"}], add_ok=False)
    cfg, notice = lark_profile.save_bot_synced(_bot(cli_profile="other"))
    assert cfg.cli_profile == "" and notice


def test_toggle_save_keeps_profile_when_cli_unavailable(monkeypatch):
    """lark-cli 暂时判不了（error）：纯开关保存保留原值，不因 CLI 抖动丢身份。"""
    _fake_channels_store(monkeypatch, [_bot(cli_profile="mine")])
    _fake_lark_cli(monkeypatch, None)
    cfg, _ = lark_profile.save_bot_synced(_bot(cli_profile="mine"))
    assert cfg.cli_profile == "mine"


def test_toggle_save_ok_profile_skips_sync(monkeypatch):
    """profile 完好：只做一次状态探测，不跑 add/remove。"""
    _fake_channels_store(monkeypatch, [_bot(cli_profile="mine")])
    calls = _fake_lark_cli(monkeypatch, [{"name": "mine", "appId": "cli_a"}])
    cfg, _ = lark_profile.save_bot_synced(_bot(cli_profile="mine"))
    assert cfg.cli_profile == "mine"
    assert all(c[:2] == ("profile", "list") for c in calls)


# ── 7. 妙记取数命令不改会话 shell 的 cwd ──────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell 行为")
def test_transcript_hint_command_keeps_shell_cwd(tmp_path):
    """按提示词里的命令在持久 shell 里跑一遍，之后 cwd 必须原地不动。

    回归：``cd <tmp> && lark-cli ...`` 把会话 shell 永久挪到临时区。
    """
    bin_dir, work, tmp = tmp_path / "bin", tmp_path / "proj", tmp_path / "tmpd"
    for d in (bin_dir, work, tmp):
        d.mkdir()
    fake = bin_dir / "lark-cli"
    fake.write_text("#!/bin/sh\nmkdir -p minutes/tok && echo hi > minutes/tok/t.txt\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    hint = minutes.transcript_hint("tok", str(tmp))
    cmd = next(line for line in hint.splitlines() if "minutes +detail" in line)
    out = subprocess.run(
        ["bash", "-c", f"{cmd.strip()}\npwd"],
        cwd=work,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
    )
    assert out.stdout.strip() == str(work)
    assert (tmp / "minutes" / "tok" / "t.txt").exists()  # 逐字稿仍落在临时区


def test_transcript_hint_command_windows_uses_pushd(monkeypatch):
    """Windows 会话 shell 是 cmd.exe：pushd/popd 包住，不留下 cwd 变更。"""
    monkeypatch.setattr(sys, "platform", "win32")
    hint = minutes.transcript_hint("tok", r"C:\t mp")
    assert (
        'pushd "C:\\t mp" && lark-cli minutes +detail --minute-tokens tok '
        "--transcript --as user & popd"
    ) in hint


# ── 10. 未设置的 ${ENV} 引用视同空值 ─────────────────────────────────────


def test_resolve_ref(monkeypatch):
    monkeypatch.setenv("LUMI_T_SET", "v")
    monkeypatch.delenv("LUMI_T_UNSET", raising=False)
    assert resolve_ref("${LUMI_T_SET}") == "v"
    assert resolve_ref("plain") == "plain"
    assert resolve_ref("${LUMI_T_UNSET}") == ""
    assert resolve_ref("${LUMI_T_SET}${LUMI_T_UNSET}") == ""


async def test_start_unset_env_ref_reports_missing_credentials(monkeypatch, tmp_path):
    """凭证引用的环境变量没设：报「缺凭证」，而不是拿字面 ${X} 去连。"""
    monkeypatch.delenv("LUMI_T_UNSET", raising=False)
    ch = _quiet_channel(
        monkeypatch,
        FeishuChannelConfig(
            app_id="${LUMI_T_UNSET}", app_secret="s", workspace=str(tmp_path)
        ),
        lambda: None,
    )
    try:
        await asyncio.wait_for(ch.start(), timeout=2)
    finally:
        ch._running = False
    assert ch.status()["detail"] == "缺少 app_id / app_secret"


def test_lark_profile_unset_env_ref_is_unconfigured(monkeypatch):
    monkeypatch.delenv("LUMI_T_UNSET", raising=False)
    calls = _fake_lark_cli(monkeypatch, [])
    ref = "${LUMI_T_UNSET}"
    assert lark_profile.profile_status(ref, "p") == ("error", "凭证未配置")
    cfg = FeishuChannelConfig(id="b1", app_id=ref, app_secret=ref)
    assert lark_profile.sync_profile(cfg) == ("", "凭证未配置")
    assert calls == []  # 没拿字面引用去问 lark-cli


def test_setup_unset_env_ref_reports_missing_credentials(monkeypatch):
    monkeypatch.delenv("LUMI_T_UNSET", raising=False)

    def no_network(a, s):
        raise AssertionError("不该拿字面 ${X} 去请求开放平台")

    monkeypatch.setattr(setup, "_fetch_version", no_network)
    checks = setup.diagnose("${LUMI_T_UNSET}", "${LUMI_T_UNSET}")
    assert checks[0]["name"] == "缺少 App ID 或 App Secret"


def test_minutes_diagnose_unset_env_ref_not_in_fix_url(monkeypatch):
    """未设置的引用不得拼进修复链接（字面 ${X} 的链接点不开）。"""
    monkeypatch.delenv("LUMI_T_UNSET", raising=False)
    monkeypatch.setattr(
        minutes.toolbox,
        "detect",
        lambda name: toolbox.ToolStatus(name=name, source="system", path="/x"),
    )
    monkeypatch.setattr(
        minutes,
        "_auth_status",
        lambda p: ({"identities": {"user": {"available": True, "scope": ""}}}, ""),
    )
    checks = minutes.diagnose("${LUMI_T_UNSET}", "lumi-x")
    scope = next(c for c in checks if c["key"] == "scope")
    assert "${" not in scope["fix_url"]


def test_same_app_unset_refs_do_not_collide(monkeypatch):
    """两条都引用未设置变量的机器人不算撞 app（空值不算撞）。"""
    monkeypatch.delenv("LUMI_T_UNSET", raising=False)
    assert not store.same_app("${LUMI_T_UNSET}", "${LUMI_T_UNSET}")


# ── 13. workspace 规范化 ────────────────────────────────────────────────


@pytest.mark.parametrize("variant", ["~/p", "{home}/p/"])
def test_one_bot_per_project_ignores_path_spelling(monkeypatch, tmp_path, variant):
    """``~/p`` / 尾斜杠与 ``/home/u/p`` 是同一项目，不能借写法差异绑第二个机器人。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    _fake_channels_store(
        monkeypatch,
        [
            {
                "id": "a",
                "app_id": "cli_a",
                "app_secret": "s",
                "workspace": f"{tmp_path}/p",
            }
        ],
    )
    ws = variant.format(home=tmp_path)
    with pytest.raises(ValueError, match="已被机器人"):
        store.validate_feishu_bot(
            {"app_id": "cli_b", "app_secret": "s", "workspace": ws}
        )


def test_validate_stores_normalized_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _fake_channels_store(monkeypatch, [])
    cfg = store.validate_feishu_bot({"app_id": "cli_b", "workspace": "~/p/"})
    assert cfg.workspace == str(tmp_path / "p")


def test_shell_env_for_expands_tilde_workspace(monkeypatch, tmp_path):
    """老条目里存的 ``~/p`` 也得命中：否则该项目 lark-cli 丢了机器人身份。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "p").mkdir()
    cfg_file = tmp_path / "lumi.json"
    cfg_file.write_text("{}")
    monkeypatch.setattr(store.user_store, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(
        store,
        "load_feishu_bots",
        lambda: [FeishuChannelConfig(workspace="~/p", cli_profile="lumi-a")],
    )
    store._workspace_profiles.cache_clear()
    assert store.shell_env_for(str(tmp_path / "p")) == {
        "LARKSUITE_CLI_PROFILE": "lumi-a"
    }


# ── 19. 技能清单为空 ≠ 0 个待装 ──────────────────────────────────────────


def test_empty_skill_manifest_is_unreadable_not_uninstalled(monkeypatch, tmp_path):
    """清单为空时「一键安装」是空操作，报「未安装」会陷入装了仍报错的死循环。"""
    monkeypatch.setattr(
        setup.toolbox,
        "detect",
        lambda name: toolbox.ToolStatus(
            name=name, source="system", path="/usr/bin/lark-cli", version="1.0.0"
        ),
    )
    monkeypatch.setattr(setup.toolbox, "lark_skill_versions", lambda path: {})
    checks = setup.local_env_checks(str(tmp_path))
    skills = next(c for c in checks if c["key"] == "skills")
    assert skills["name"] == "无法读取飞书技能清单"
    assert not skills["fix_action"]


def test_quoted_tmp_dir_in_hint_posix(monkeypatch):
    """带空格 / 引号的临时区路径在 POSIX 命令里被正确引用。"""
    monkeypatch.setattr(sys, "platform", "linux")
    tmp = "/t/a b'c"
    assert f"(cd {shlex.quote(tmp)} && lark-cli " in minutes.transcript_hint("x", tmp)

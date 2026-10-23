"""设备码授权生命周期：不调用飞书、不读取真实用户 token。"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lumi.gateway import channel_rpc
from lumi.gateway.channels.feishu import lark_profile, minutes, minutes_auth
from lumi.gateway.channels.feishu.scopes import MINUTES_SCOPES


@pytest.fixture
def cli(monkeypatch):
    calls = []
    monkeypatch.setattr(minutes_auth, "profile_status", lambda app, profile: ("ok", ""))

    async def run(*args, profile, **kwargs):
        calls.append((args, profile, kwargs))
        if args == ("config", "strict-mode"):
            return 0, "strict-mode: off (source: global (default))\n"
        if "--no-wait" in args:
            return 0, json.dumps(
                {
                    "device_code": f"secret-{profile}",
                    "verification_url": "https://accounts.feishu.cn/verify?code=abc%2Bdef",
                    "expires_in": 180,
                    "hint": "untrusted CLI instructions",
                }
            )
        if "qrcode" in args:
            Path(kwargs["cwd"], "auth.png").write_bytes(b"PNG fixture")
            return 0, ""
        return 0, json.dumps({"event": "authorization_complete"})

    monkeypatch.setattr(minutes_auth, "run_cli_async", run)
    return calls


@pytest.fixture(autouse=True)
def clear_flows():
    yield
    for auth_id in tuple(minutes_auth._flows):
        minutes_auth.cancel(auth_id)


async def test_rpc_authorizes_only_the_robot_profile_and_keeps_device_code_on_backend(
    cli,
):
    started = await channel_rpc.HANDLERS["start_minutes_auth"](
        {
            "config": {"app_id": "cli-a", "cli_profile": "bot-a"},
        }
    )
    assert started["status"] == "waiting"
    assert 0 < started["expires_in"] <= 180
    assert "secret" not in json.dumps(started) and "hint" not in started
    assert base64.b64decode(started["qr_code"].split(",", 1)[1]) == b"PNG fixture"
    assert not Path(cli[2][2]["cwd"]).exists()  # 二维码临时文件已删除
    assert all(profile == "bot-a" for _, profile, _ in cli)
    assert cli[1][0][-1] == ",".join(MINUTES_SCOPES)
    assert cli[2][0][2] == started["verification_url"]  # opaque URL 原样编码
    result = await channel_rpc.HANDLERS["finish_minutes_auth"](
        {"auth_id": started["auth_id"]}
    )
    assert result == {"status": "authorized"}
    assert cli[3][1] == "bot-a" and "secret-bot-a" in cli[3][0]
    assert not minutes_auth._flows


async def test_mismatched_robot_profile_never_starts_login(cli, monkeypatch):
    monkeypatch.setattr(minutes_auth, "profile_status", lambda *_: ("mismatch", ""))
    with pytest.raises(ValueError, match="保存机器人"):
        await minutes_auth.start("cli-a", "bot-b")
    assert cli == []


@pytest.mark.parametrize("consent", [None, False, "true", 1])
async def test_bot_restriction_requires_explicit_boolean_consent(
    cli, monkeypatch, consent
):
    async def restricted(*args, profile, **kwargs):
        assert args == ("config", "strict-mode")
        assert profile == "bot-a"
        return 0, "strict-mode: bot (source: global)\n"

    monkeypatch.setattr(minutes_auth, "run_cli_async", restricted)
    result = await channel_rpc.HANDLERS["start_minutes_auth"](
        {
            "config": {"app_id": "cli-a", "cli_profile": "bot-a"},
            "allow_user_identity": consent,
        }
    )
    assert result == {"status": "restricted"}
    assert not minutes_auth._flows


async def test_consent_changes_only_selected_profile_then_starts_qr(cli, monkeypatch):
    original = minutes_auth.run_cli_async
    policy_calls = []
    mode = "bot"

    async def run(*args, profile, **kwargs):
        nonlocal mode
        if args[:2] == ("config", "strict-mode"):
            policy_calls.append((args, profile))
            if len(args) == 3:
                assert args[2] == "off"
                mode = "off"
                return 0, "Strict mode set to off (profile)"
            return 0, f"strict-mode: {mode} (source: global)"
        assert mode == "off"
        return await original(*args, profile=profile, **kwargs)

    monkeypatch.setattr(minutes_auth, "run_cli_async", run)
    result = await channel_rpc.HANDLERS["start_minutes_auth"](
        {
            "config": {"app_id": "cli-a", "cli_profile": "bot-a"},
            "allow_user_identity": True,
        }
    )
    assert result["status"] == "waiting"
    assert policy_calls == [
        (("config", "strict-mode"), "bot-a"),
        (("config", "strict-mode", "off"), "bot-a"),
        (("config", "strict-mode"), "bot-a"),
    ]
    assert (await minutes_auth.finish(result["auth_id"]))["status"] == "authorized"


@pytest.mark.parametrize("mode", ["off", "user"])
async def test_allowed_modes_never_change_policy_even_with_consent(
    cli, monkeypatch, mode
):
    original = minutes_auth.run_cli_async

    async def run(*args, **kwargs):
        if args[:2] == ("config", "strict-mode"):
            assert len(args) == 2
            return 0, f'strict-mode: {mode} (source: profile "bot-a")'
        return await original(*args, **kwargs)

    monkeypatch.setattr(minutes_auth, "run_cli_async", run)
    assert (await minutes_auth.start("cli-a", "bot-a", allow_user_identity=True))[
        "status"
    ] == "waiting"


@pytest.mark.parametrize("failure", ["read", "write", "provider"])
async def test_policy_failure_never_starts_login(cli, monkeypatch, failure):
    async def run(*args, **kwargs):
        assert args[:2] == ("config", "strict-mode")
        if failure == "read":
            return 1, "read failed"
        if len(args) == 3:
            return (1, "write failed") if failure == "write" else (0, "saved")
        return 0, "strict-mode: bot (source: credential provider)"

    monkeypatch.setattr(minutes_auth, "run_cli_async", run)
    with pytest.raises(ValueError):
        await minutes_auth.start("cli-a", "bot-a", allow_user_identity=True)
    assert not minutes_auth._flows


async def test_consent_does_not_bypass_profile_validation(cli, monkeypatch):
    monkeypatch.setattr(minutes_auth, "profile_status", lambda *_: ("mismatch", ""))
    with pytest.raises(ValueError, match="保存机器人"):
        await minutes_auth.start("cli-a", "bot-b", allow_user_identity=True)
    assert cli == []


@pytest.mark.parametrize("token_status", ["valid", "needs_refresh"])
async def test_logged_in_bot_restriction_offers_repair_without_changing_policy(
    cli, monkeypatch, token_status
):
    monkeypatch.setattr(
        minutes.toolbox, "detect", lambda _: SimpleNamespace(source="system")
    )
    calls = []

    def run(*args, profile):
        calls.append((args, profile))
        assert profile == "bot-a"
        if args == ("auth", "status", "--json"):
            return 0, json.dumps(
                {
                    "identities": {
                        "user": {
                            "available": True,
                            "tokenStatus": token_status,
                            "scope": " ".join(MINUTES_SCOPES),
                        }
                    }
                }
            )
        if args[:2] == ("api", "POST"):
            return 1, json.dumps(
                {
                    "error": {
                        "message": 'strict mode is "bot", only bot-identity commands are available'
                    }
                }
            )
        assert args == ("config", "strict-mode"), "Diagnosis must not modify policy"
        return 0, "strict-mode: bot (source: global)"

    monkeypatch.setattr(minutes, "run_cli", run)
    checks = minutes.diagnose("cli-a", "bot-a")
    auth = next(check for check in checks if check["key"] == "auth")
    assert auth["tone"] == "error" and auth["fix_auth"]
    assert "用户" in auth["name"] and not auth["fix_url"]
    assert any(args == ("config", "strict-mode") for args, _ in calls)
    assert not minutes_auth._flows

    # Existing UI authorizes auth.fix_auth through start, which must still require consent.
    async def restricted(*args, profile, **kwargs):
        assert args == ("config", "strict-mode") and profile == "bot-a"
        return 0, "strict-mode: bot (source: global)"

    monkeypatch.setattr(minutes_auth, "run_cli_async", restricted)
    assert await minutes_auth.start("cli-a", "bot-a") == {"status": "restricted"}


@pytest.mark.parametrize("policy", ["off", "user", "unreadable"])
def test_subscription_errors_are_not_misreported_as_identity_restrictions(
    monkeypatch, policy
):
    monkeypatch.setattr(
        minutes.toolbox, "detect", lambda _: SimpleNamespace(source="system")
    )
    monkeypatch.setattr(
        minutes,
        "_auth_status",
        lambda _: (
            {
                "identities": {
                    "user": {
                        "available": True,
                        "scope": " ".join(MINUTES_SCOPES),
                    }
                }
            },
            "",
        ),
    )
    monkeypatch.setattr(minutes, "ensure_subscription", lambda _: "网络连接失败")
    monkeypatch.setattr(
        minutes,
        "run_cli",
        lambda *a, **kw: (
            (1, "read failed")
            if policy == "unreadable"
            else (0, f"strict-mode: {policy} (source: global)")
        ),
    )
    checks = minutes.diagnose("cli-a", "bot-a")
    assert checks[-1]["key"] == "subscription"
    assert checks[-1]["detail"] == "网络连接失败"
    assert not any(check["fix_auth"] for check in checks)


async def test_partial_scope_grant_still_completes_login_for_followup_diagnosis(
    cli, monkeypatch
):
    started = await minutes_auth.start("cli-a", "bot-a")

    async def partial(*args, **kwargs):
        return 3, json.dumps(
            {
                "event": "authorization_complete",
                "warning": {"type": "missing_scope"},
                "missing": [MINUTES_SCOPES[1]],
            }
        )

    monkeypatch.setattr(minutes_auth, "run_cli_async", partial)
    assert await minutes_auth.finish(started["auth_id"]) == {"status": "authorized"}
    assert not minutes_auth._flows


async def test_cancel_stops_pending_finish_without_affecting_other_robot(
    cli, monkeypatch
):
    a = await minutes_auth.start("cli-a", "bot-a")
    b = await minutes_auth.start("cli-b", "bot-b")
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def waiting(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    monkeypatch.setattr(minutes_auth, "run_cli_async", waiting)
    pending = asyncio.create_task(minutes_auth.finish(a["auth_id"]))
    await entered.wait()
    minutes_auth.cancel(a["auth_id"])
    assert await pending == {"status": "cancelled"}
    assert stopped.is_set()
    assert b["auth_id"] in minutes_auth._flows
    assert minutes_auth.cancel(a["auth_id"]) == {"status": "cancelled"}


async def test_expiry_stops_waiting_and_releases_device_code(cli, monkeypatch):
    started = await minutes_auth.start("cli-a", "bot-a")
    flow = minutes_auth._flows[started["auth_id"]]
    flow.expiry.cancel()
    flow.deadline = asyncio.get_running_loop().time() + 0.02
    flow.expiry = asyncio.get_running_loop().call_later(
        0.02, minutes_auth.cancel, started["auth_id"]
    )

    async def waiting(*args, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(minutes_auth, "run_cli_async", waiting)
    assert await minutes_auth.finish(started["auth_id"]) == {"status": "expired"}
    assert not minutes_auth._flows


async def test_connection_task_cancellation_propagates_and_cleans_flow(
    cli, monkeypatch
):
    started = await minutes_auth.start("cli-a", "bot-a")
    entered = asyncio.Event()

    async def waiting(*args, **kwargs):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(minutes_auth, "run_cli_async", waiting)
    pending = asyncio.create_task(minutes_auth.finish(started["auth_id"]))
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not minutes_auth._flows


@pytest.mark.parametrize("out", ['{"error":{"message":"用户拒绝授权"}}', "not JSON"])
async def test_failed_login_releases_flow_and_does_not_claim_success(
    cli, monkeypatch, out
):
    started = await minutes_auth.start("cli-a", "bot-a")

    async def rejected(*args, **kwargs):
        return 1, out

    monkeypatch.setattr(minutes_auth, "run_cli_async", rejected)
    with pytest.raises(ValueError):
        await minutes_auth.finish(started["auth_id"])
    assert not minutes_auth._flows


async def test_async_runner_preserves_profile_cwd_and_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(lark_profile.shutil, "which", lambda _: sys.executable)
    code, out = await lark_profile.run_cli_async(
        "-c",
        "import os; print(os.environ['LARKSUITE_CLI_PROFILE']); print(os.getcwd()); print('已授权')",
        profile="bot-a",
        cwd=str(tmp_path),
    )
    assert code == 0 and out.splitlines() == ["bot-a", str(tmp_path), "已授权"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 进程组回收")
async def test_async_runner_cancel_kills_npm_wrapper_and_child(tmp_path, monkeypatch):
    monkeypatch.setattr(lark_profile.shutil, "which", lambda _: sys.executable)
    marker = tmp_path / "should-not-exist"
    ready = tmp_path / "ready"
    child = f"import time,pathlib; time.sleep(.4); pathlib.Path({str(marker)!r}).touch(); time.sleep(30)"
    code = f"import subprocess,sys,time,pathlib; subprocess.Popen([sys.executable,'-c',{child!r}]); pathlib.Path({str(ready)!r}).touch(); time.sleep(30)"
    pending = asyncio.create_task(
        lark_profile.run_cli_async("-c", code, profile="bot-a")
    )
    async with asyncio.timeout(3):
        while not ready.exists():
            await asyncio.sleep(0.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await asyncio.sleep(0.5)
    assert not marker.exists()

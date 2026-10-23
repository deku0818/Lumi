"""设备码授权生命周期：不调用飞书、不读取真实用户 token。"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import pytest

from lumi.gateway import channel_rpc
from lumi.gateway.channels.feishu import lark_profile, minutes_auth
from lumi.gateway.channels.feishu.scopes import MINUTES_SCOPES


@pytest.fixture
def cli(monkeypatch):
    calls = []
    monkeypatch.setattr(minutes_auth, "profile_status", lambda app, profile: ("ok", ""))

    async def run(*args, profile, **kwargs):
        calls.append((args, profile, kwargs))
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
    assert not Path(cli[1][2]["cwd"]).exists()  # 二维码临时文件已删除
    assert cli[0][1] == cli[1][1] == "bot-a"
    assert cli[0][0][-1] == ",".join(MINUTES_SCOPES)
    assert cli[1][0][2] == started["verification_url"]  # opaque URL 原样编码
    result = await channel_rpc.HANDLERS["finish_minutes_auth"](
        {"auth_id": started["auth_id"]}
    )
    assert result == {"status": "authorized"}
    assert cli[2][1] == "bot-a" and "secret-bot-a" in cli[2][0]
    assert not minutes_auth._flows


async def test_mismatched_robot_profile_never_starts_login(cli, monkeypatch):
    monkeypatch.setattr(minutes_auth, "profile_status", lambda *_: ("mismatch", ""))
    with pytest.raises(ValueError, match="保存机器人"):
        await minutes_auth.start("cli-a", "bot-b")
    assert cli == []


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

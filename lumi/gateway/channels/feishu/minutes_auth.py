"""妙记设备码授权：二维码留在当前设置页，token 始终由 lark-cli 保管。"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from lumi.gateway.channels.feishu.lark_profile import (
    parse_strict_mode,
    profile_status,
    run_cli_async,
)
from lumi.gateway.channels.feishu.scopes import MINUTES_SCOPES


@dataclass
class _Flow:
    profile: str
    device_code: str
    deadline: float
    expiry: asyncio.TimerHandle
    task: asyncio.Task | None = None


_flows: dict[str, _Flow] = {}


def cancel(auth_id: str) -> dict:
    """幂等取消；二维码过期 / 关闭表单时回收等待中的整个进程组。"""
    flow = _flows.pop(auth_id, None)
    if flow:
        flow.expiry.cancel()
        if flow.task:
            flow.task.cancel()
    return {"status": "cancelled"}


def _payload(code: int, out: str) -> dict:
    try:
        data = json.loads(out)
    except ValueError:
        raise ValueError("无法读取飞书授权结果，请检查 lark-cli 版本后重试") from None
    if not isinstance(data, dict):
        raise ValueError("无法读取飞书授权结果，请检查 lark-cli 版本后重试")
    # CLI 在「已登录但部分 scope 未授予」时也用非零退出码，token 已保存。
    # 让 desktop 接着体检，展示缺失权限与开放平台入口，不能把它报成扫码失败。
    if code and data.get("event") != "authorization_complete":
        error = data.get("error", {})
        message = error.get("message") if isinstance(error, dict) else error
        raise ValueError(message or "飞书授权失败，请重试")
    return data


async def _strict_mode(profile: str) -> str:
    return parse_strict_mode(
        *await run_cli_async("config", "strict-mode", profile=profile)
    )


async def start(
    app_id: str, profile: str, *, allow_user_identity: bool = False
) -> dict:
    """校验机器人身份 → 获取设备码 → 本地生成 PNG；不向 desktop 暴露设备码。"""
    status, detail = await asyncio.to_thread(profile_status, app_id, profile)
    if status != "ok":
        raise ValueError(detail or "请先保存机器人，同步 lark-cli 身份后再授权")
    try:
        if await _strict_mode(profile) == "bot":
            if not allow_user_identity:
                return {"status": "restricted"}
            code, _ = await run_cli_async(
                "config", "strict-mode", "off", profile=profile
            )
            if code:
                raise ValueError(
                    "无法允许当前机器人使用用户身份，请检查配置写入权限后重试"
                )
            if await _strict_mode(profile) == "bot":
                raise ValueError("当前环境仍限制用户身份，请联系该机器的管理员处理")
        data = _payload(
            *await run_cli_async(
                "auth",
                "login",
                "--no-wait",
                "--json",
                "--recommend",
                "--scope",
                ",".join(MINUTES_SCOPES),
                profile=profile,
            )
        )
        url = data.get("verification_url")
        device_code = data.get("device_code")
        expires_in = data.get("expires_in")
        if (
            not url
            or not device_code
            or not isinstance(expires_in, int)
            or expires_in <= 0
        ):
            raise ValueError("飞书未返回有效的授权二维码，请重试")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + expires_in
        with TemporaryDirectory(prefix="lumi-feishu-auth-") as tmp:
            code, _ = await run_cli_async(
                "auth",
                "qrcode",
                url,
                "--output",
                "auth.png",
                "--size",
                "256",
                profile=profile,
                cwd=tmp,
            )
            if code:
                raise ValueError("授权二维码生成失败，请检查 lark-cli 版本后重试")
            png = base64.b64encode(Path(tmp, "auth.png").read_bytes()).decode("ascii")
    except TimeoutError:
        raise ValueError("连接飞书授权超时，请检查网络后重试") from None

    auth_id = uuid4().hex
    remaining = deadline - loop.time()
    if remaining <= 0:
        return {"status": "expired"}
    _flows[auth_id] = _Flow(
        profile, device_code, deadline, loop.call_later(remaining, cancel, auth_id)
    )
    return {
        "status": "waiting",
        "auth_id": auth_id,
        "verification_url": url,
        "qr_code": f"data:image/png;base64,{png}",
        "expires_in": remaining,
    }


async def finish(auth_id: str) -> dict:
    """由 CLI 等待扫码并保存 token；返回后 desktop 自动运行妙记体检 / 重建订阅。"""
    flow = _flows.get(auth_id)
    if flow is None:
        return {"status": "expired"}
    if flow.task:
        raise ValueError("正在等待此二维码的授权结果")
    flow.task = asyncio.current_task()
    try:
        remaining = flow.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return {"status": "expired"}
        data = _payload(
            *await run_cli_async(
                "auth",
                "login",
                "--device-code",
                flow.device_code,
                "--json",
                profile=flow.profile,
                timeout=remaining,
            )
        )
        if data.get("event") != "authorization_complete":
            raise ValueError("飞书未确认授权完成，请重试")
        return {"status": "authorized"}
    except TimeoutError:
        return {"status": "expired"}
    except asyncio.CancelledError:
        # 主动取消已移除 flow，应答挂起的 finish RPC；连接收尾取消仍正常传播。
        if auth_id in _flows:
            raise
        return {
            "status": "expired"
            if asyncio.get_running_loop().time() >= flow.deadline
            else "cancelled"
        }
    finally:
        _flows.pop(auth_id, None)
        flow.expiry.cancel()

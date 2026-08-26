"""运维侧：包自更新与服务健康探测。

Lumi 以 PyPI 包（``lumi-harness``，命令仍叫 ``lumi``）分发，**进程生命周期不归它管**
——systemd / docker / Electron sidecar / 前台 Ctrl-C，谁拉起来的谁负责。一个 PyPI 包
自己做守护永远做不过 systemd（没有崩溃拉起、没有开机自启、没有资源限制），做半套
反而让人以为「lumi 会守着」。所以这里只回答两个 lumi 自己才知道答案的问题：这个包
该怎么升级，以及那个跑着的服务到底通不通。
"""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path
from time import sleep

PKG = "lumi-harness"
PYPI_JSON = f"https://pypi.org/pypi/{PKG}/json"
PROBE_TIMEOUT = 10.0

# ── 自更新 ────────────────────────────────────────────────────────────────


def install_kind() -> str:
    """本进程这个 lumi 是怎么装的：``uv-tool`` | ``pipx`` | ``pip`` | ``source`` | ``frozen``。

    看 ``sys.prefix`` 下安装器自己留的落款，而不是匹配 ``~/.local/share/uv/tools``
    这类路径——工具目录可被 ``UV_TOOL_DIR`` / ``PIPX_HOME`` 改道，路径匹配会漏判，
    落款文件不会。
    """
    if getattr(sys, "frozen", False):
        return "frozen"  # 桌面打包版：后端跟着应用走，自更新是 Electron updater 的事
    root = Path(sys.prefix)
    if (root / "uv-receipt.toml").exists():
        return "uv-tool"
    if (root / "pipx_metadata.json").exists():
        return "pipx"
    if _editable():
        return "source"
    return "pip"


def _editable() -> bool:
    """可编辑安装（源码开发树）：dist-info 里 direct_url.json 会标 editable（PEP 610）。"""
    raw = distribution(PKG).read_text("direct_url.json")
    if not raw:
        return False  # 从 PyPI 装的没有这个文件
    return bool(json.loads(raw).get("dir_info", {}).get("editable"))


def upgrade_command(kind: str, target: str) -> list[str]:
    """升级命令。target 为空 = 装最新；给了版本号 = 装那一版（同样用于回退旧版）。

    一律 ``install --force``，不走 ``uv tool upgrade`` / ``pipx upgrade``——**装过时带过
    版本 pin 的话，upgrade 子命令会一声不吭地什么都不做**（uv 实测输出 "Nothing to
    upgrade"，退出码 0），而部署脚本装指定版本正是这么装的。退出码 0 会被上层当成
    升级成功，用户下次看到的还是旧版。``@latest`` / 裸包名重新解析则会清掉那个 pin。
    """
    if kind == "uv-tool":
        spec = f"{PKG}=={target}" if target else f"{PKG}@latest"
        return [_uv_path(), "tool", "install", "--force", spec]
    spec = f"{PKG}=={target}" if target else PKG
    if kind == "pipx":
        return ["pipx", "install", "--force", spec]
    return [sys.executable, "-m", "pip", "install", "--upgrade", spec]


def _uv_path() -> str:
    """uv 可执行文件：系统 PATH 优先，回落 Lumi 工具箱（与 env 命令同一套解析）。"""
    from lumi.gateway.toolbox import locate

    found = shutil.which("uv") or locate("uv").path
    if not found:
        raise RuntimeError("找不到 uv——这个 lumi 是 uv tool 装的，升级也得由它来做")
    return found


def latest_version() -> str:
    """PyPI 上的最新版；查不到返回空串。

    网络不通不该挡住升级本身（国内机器常连不上 pypi.org 却配了镜像源），调用方
    拿到空串就跳过「是否已最新」的判断，直接把升级命令交给包管理器去试。
    """
    try:
        with urllib.request.urlopen(PYPI_JSON, timeout=10) as resp:
            return json.load(resp)["info"]["version"]
    except (OSError, ValueError, KeyError):
        return ""


# ── 服务健康 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServiceStatus:
    """一次探测的结论。

    ``state``：``guarded`` 活着且鉴权生效 / ``unguarded`` 活着但谁都能连 /
    ``down`` 没在跑 / ``error`` 端口有东西但不是能用的 Lumi。
    """

    state: str
    detail: str = ""

    @property
    def running(self) -> bool:
        return self.state in ("guarded", "unguarded")


def check_service(host: str, port: int, token: str = "") -> ServiceStatus:
    """探一次服务——**不需要令牌也能给出完整结论**。

    拿一个必然错误的令牌连过去：被 1008 拒 = 服务活着且鉴权生效；居然连上还拿到
    result = 服务活着但没设令牌、**谁都能连**；连不上 = 没在跑。三种状态互斥，而
    「进程活着」「端口开着」这类指标区分不出中间那一种——那恰恰是最该被喊出来的
    一种（agent 的 bash 与文件工具直接作用于这台机器）。

    给了令牌就再跑一条正向探针：证明这台服务真能干活。端口开着但引擎卡死时，负向
    探针照样会痛快回绝，只看它会把一台死机报成健康。
    """
    url = f"ws://{host}:{port}/ws"
    outcome, detail = asyncio.run(_probe(f"{url}?token=probe-{secrets.token_hex(8)}"))
    if outcome == "down":
        return ServiceStatus("down", detail)
    if outcome == "error":
        return ServiceStatus("error", detail)
    if outcome == "ok":
        return ServiceStatus("unguarded")
    if not token:
        return ServiceStatus("guarded")
    positive, why = asyncio.run(_probe(f"{url}?token={token}"))
    if positive == "ok":
        return ServiceStatus("guarded", "令牌可用，list_sessions 正常")
    if positive == "rejected":
        return ServiceStatus("error", "服务在跑，但你给的令牌被拒绝")
    return ServiceStatus("error", why or "服务无法应答")


async def _probe(url: str) -> tuple[str, str]:
    """连一次 WS 发 list_sessions：``ok`` 拿到 result / ``rejected`` 被拒 / ``down`` 连不上。"""
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException

    try:
        async with websockets.connect(url, open_timeout=PROBE_TIMEOUT) as ws:
            await ws.send(
                json.dumps({"id": "probe", "method": "list_sessions", "params": {}})
            )
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), PROBE_TIMEOUT))
                if frame.get("id") == "probe":
                    if "result" in frame:
                        return "ok", ""
                    return "error", str(frame.get("error"))
    except ConnectionClosed as exc:
        # 服务端「先 accept 再校验」，令牌不对以 1008 关闭（见 gateway/channels/ws.py）
        if exc.code == 1008:
            return "rejected", ""
        return "error", f"连接被关闭（code {exc.code}）"
    except TimeoutError:
        return "error", "超时未响应"
    except ConnectionRefusedError:
        return "down", ""  # 没人监听，地址已在上层打出来了，errno 再复述一遍是噪音
    except OSError as exc:
        return "down", str(exc)  # 路由不可达 / DNS 之类，远程探测时这句才是线索
    except WebSocketException as exc:
        return "error", f"端口上的不是 Lumi 服务（{exc}）"


# ── 日志 ──────────────────────────────────────────────────────────────────


def log_file() -> Path:
    """服务与 agent 的运行日志，与 utils/logger.py 同一份。"""
    from lumi.utils.paths import lumi_home

    return lumi_home() / "logs" / "Lumi.log"


def tail(path: Path, lines: int) -> list[str]:
    """末尾 N 行。只读文件尾部——这份日志没有轮转，整读会把几百 MB 拉进内存。"""
    with path.open("rb") as handle:
        size = handle.seek(0, 2)
        handle.seek(max(0, size - 256 * 1024))
        data = handle.read()
    return data.decode("utf-8", "replace").splitlines()[-lines:]


def follow(path: Path) -> Iterator[str]:
    """从文件末尾起持续吐新行，直到调用方中断。"""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        buffer = b""
        while True:
            chunk = handle.read(65536)
            if not chunk:
                sleep(0.5)
                continue
            buffer += chunk
            *ready, buffer = buffer.split(b"\n")
            for line in ready:
                yield line.decode("utf-8", "replace")


def run(cmd: list[str]) -> int:
    """跑升级命令，输出原样透传给用户（包管理器自己的进度条比我们转述的强）。"""
    return subprocess.run(cmd).returncode

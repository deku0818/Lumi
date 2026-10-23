"""Project native Agent opt-in and shared read-only CLI availability checks."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal

from lumi.agents.tools.loader import AgentConfig
from lumi.utils.atomic_io import atomic_write_json, read_json_object_for_update
from lumi.utils.cli import run_cli, version_from_output
from lumi.utils.logger import logger

from .executables import resolve_cli

NativeName = Literal["codex", "claude"]
NATIVE_AGENTS = {
    "codex": AgentConfig(
        name="codex",
        description="使用 Codex 作为agent。Codex 根据工作目录读取项目约束、读取和存储项目记忆，必须明确指定任务所属项目的 cwd。",
        executor="codex",
        system_prompt="",
    ),
    "claude": AgentConfig(
        name="claude",
        description="使用 Claude Code 作为agent。Claude Code 根据工作目录读取项目约束、读取和存储项目记忆，必须明确指定任务所属项目的 cwd。",
        executor="claude_code",
        system_prompt="",
    ),
}


@dataclass(frozen=True)
class AgentCheck:
    name: NativeName
    state: Literal["ready", "missing", "login", "error"]
    path: str = ""
    version: str = ""


_cache: dict[str, tuple[float, AgentCheck]] = {}
_locks = {name: Lock() for name in NATIVE_AGENTS}


def check_agent(name: NativeName, refresh: bool = False) -> AgentCheck:
    with _locks[name]:
        try:
            path = resolve_cli(name)
        except RuntimeError:
            _cache.pop(name, None)
            return AgentCheck(name, "missing")
        cached = _cache.get(name)
        if (
            not refresh
            and cached
            and cached[1].path == path
            and time.monotonic() - cached[0] < 30
        ):
            return cached[1]
        ok, output = run_cli([path, "--version"], timeout=10)
        version = version_from_output(output)
        state = "error"
        if ok:
            args = ["login", "status"] if name == "codex" else ["auth", "status"]
            ok, output = run_cli([path, *args], timeout=10)
            if name == "codex":
                text = output.lower()
                if "not logged in" in text:
                    state = "login"
                elif ok and "logged in" in text:
                    state = "ready"
            else:
                try:
                    logged_in = json.loads(output).get("loggedIn")
                except (ValueError, AttributeError):
                    logged_in = None
                if logged_in is False:
                    state = "login"
                elif ok and logged_in is True:
                    state = "ready"
        result = AgentCheck(name, state, path, version)
        _cache[name] = time.monotonic(), result
        return result


def check_agents(
    names: tuple[NativeName, ...], refresh: bool = False
) -> list[AgentCheck]:
    if not names:
        return []
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        return list(pool.map(lambda name: check_agent(name, refresh), names))


def enabled_agents(project_dir: str | Path | None) -> dict[NativeName, bool]:
    if project_dir is None:
        return {}
    path = Path(project_dir).expanduser().resolve() / ".lumi" / "agents.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("无法读取项目 Agent 配置: %s", path)
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        name: data[name] for name in NATIVE_AGENTS if isinstance(data.get(name), bool)
    }


def set_enabled(project_dir: Path, name: NativeName, enabled: bool) -> None:
    path = project_dir.expanduser().resolve() / ".lumi" / "agents.json"
    data = read_json_object_for_update(path)
    data[name] = enabled
    atomic_write_json(path, data)


def available_agents(project_dir: str | Path | None) -> list[AgentConfig]:
    enabled = enabled_agents(project_dir)
    checks = check_agents(tuple(name for name in NATIVE_AGENTS if enabled.get(name)))
    enabled = enabled_agents(project_dir)
    return [
        NATIVE_AGENTS[check.name]
        for check in checks
        if check.state == "ready" and enabled.get(check.name)
    ]

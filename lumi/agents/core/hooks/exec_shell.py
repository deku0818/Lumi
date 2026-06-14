"""Shell command hook wrapper：subprocess 协议 + 超时 + stdout 上限 + env 白名单。

把 hooks.json 配置的 shell command（``command`` 字段）包装为 Python ``Hook``：
- 启动 subprocess，stdin 喂 ``protocol.serialize_input`` 输出
- stdout 读到上限 / 进程退出，``protocol.parse_output`` 翻译为 ``HookResult``
- 5 秒默认超时；到点 SIGTERM → 1s 后 SIGKILL
- env 仅传 ``LUMI_HOOK_*`` 前缀变量 + ``PATH``，防 secrets 泄露
- ``matcher`` 正则：仅 PreToolUse / PostToolUse 生效，未命中则跳过 subprocess
- exit code: 0=正常解析 stdout / 2=deny / 其他=非阻断 error（放行）
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from lumi.agents.core.hooks.protocol import (
    matches_tool_filter,
    parse_output,
    serialize_input,
    warn_matcher_unused,
)
from lumi.agents.core.hooks.schema import (
    AdditionalContext,
    Block,
    Hook,
    HookContext,
    HookEvent,
    HookResult,
)
from lumi.utils.logger import logger

DEFAULT_TIMEOUT_MS = 5000
"""单 hook 默认 5s 超时——Lumi 是交互 agent，太长会让用户干等。"""

STDOUT_LIMIT_BYTES = 10 * 1024 * 1024
"""stdout 上限 10 MB。超限截断 + 标记 error。"""

KILL_GRACE_SECONDS = 1.0
"""SIGTERM 后等待时长，超时再 SIGKILL。"""

ENV_PASSTHROUGH_PREFIX = "LUMI_HOOK_"
"""仅 ``LUMI_HOOK_*`` 前缀环境变量透传，防 secrets（API_KEY / DB_URL 等）泄露。"""

_env_cache: dict[str, str] | None = None


def _filter_env() -> dict[str, str]:
    """构造 subprocess env：仅白名单前缀 + PATH（module-level cache）。

    cache 没 invalidate API——测试需要重置时直接把 ``_env_cache`` 置 None。
    """
    global _env_cache
    if _env_cache is None:
        env = {"PATH": os.environ.get("PATH", "")}
        for k, v in os.environ.items():
            if k.startswith(ENV_PASSTHROUGH_PREFIX):
                env[k] = v
        _env_cache = env
    return _env_cache


def make_shell_hook(
    *,
    event: HookEvent,
    command: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    matcher: str | None = None,
) -> Hook:
    """构造一个 Shell command hook，可直接 ``register_hook(event, hook)``。

    启动期校验 command 必须是绝对路径 + 存在 + 可执行——不通过抛 ``ValueError``，
    由 ``config_loader`` 捕获后 log 跳过该条（不让坏配置静默漂移到运行时）。
    """
    if not command.startswith("/"):
        raise ValueError(
            f"make_shell_hook: command must be an absolute path, got {command!r}"
        )
    if not os.path.isfile(command):
        raise ValueError(f"make_shell_hook: command not found: {command}")
    if not os.access(command, os.X_OK):
        raise ValueError(f"make_shell_hook: command not executable: {command}")
    pattern = re.compile(matcher) if matcher else None
    label = f"shell:{command}"
    warn_matcher_unused(event, matcher, label)

    async def _hook(ctx: HookContext) -> HookResult:
        if not matches_tool_filter(pattern, event, ctx.payload):
            return None
        stdin_payload = serialize_input(event, ctx).encode("utf-8")
        proc = await asyncio.create_subprocess_exec(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_filter_env(),
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=stdin_payload),
                timeout=timeout_ms / 1000,
            )
        except TimeoutError:
            logger.warning("[hooks] %s 超时 %dms，发送 SIGTERM", label, timeout_ms)
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
            except TimeoutError:
                logger.warning("[hooks] %s SIGTERM 后未退出，发送 SIGKILL", label)
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return Block(f"hook timeout after {timeout_ms}ms")

        exit_code = proc.returncode if proc.returncode is not None else -1

        if len(stdout_b) > STDOUT_LIMIT_BYTES:
            logger.warning(
                "[hooks] %s stdout 超限 %dB，截断 + 视为 error", label, len(stdout_b)
            )
            stdout_b = stdout_b[:STDOUT_LIMIT_BYTES]
            exit_code = max(exit_code, 1)  # 强制 outcome != success

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if stderr.strip():
            logger.debug("[hooks] %s stderr: %s", label, stderr[:1000])

        if exit_code == 0:
            return parse_output(stdout, source=label)
        if exit_code == 2:
            reason = (stderr.strip() or stdout.strip() or "blocked by hook")[:500]
            return Block(reason)
        # exit_code == 1 或其他 → non-blocking error，记 warn 但放行
        logger.warning(
            "[hooks] %s exit=%d，视为 passthrough; stderr=%s",
            label,
            exit_code,
            stderr[:200],
        )
        # 仍尝试解析 stdout 里的 additionalContext（hook 可在错误退出时同时给提示）
        result = parse_output(stdout, source=label)
        if isinstance(result, AdditionalContext):
            return result
        return None

    _hook.__name__ = f"shell_hook_{Path(command).name}"
    return _hook

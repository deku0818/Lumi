"""IM 渠道直连本机编程 CLI（/direct）：绑定状态 sidecar + Claude Code 无头轮驱动。

直连（relay）是 thread 级路由开关：激活后入站消息不进 LumiAgent，改为每条消息拉一次
``claude -p`` 子进程续接 cc 会话（``--resume``），stream-json 事件折叠回渠道流式卡片。
Lumi 自身的会话原封不动，退出直连即无缝回到原对话。

本模块渠道无关：飞书侧只消费 :class:`RelayEvent`（见 feishu/relay_turn.py），未来
codex runner / 其它渠道复用同一事件形状。

关键事实（调研自 cc headless 行为）：
- ``-p`` 下 stream-json 必须搭配 ``--verbose``；
- 每轮 ``--resume`` 都派生**新的** session_id——init 与 result 事件携带的 sid 都要
  落盘，续对话恒 resume 最新那个；
- init 事件是第一行输出，sid 在任何产出之前就已可持久化，中断不丢续接能力。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from lumi.agents.runtime.bg_process import terminate_group
from lumi.utils.config.global_manager import GLOBAL_CONFIG_DIR
from lumi.utils.json_sidecar import load_sidecar, update_sidecar
from lumi.utils.logger import logger

# 单行 JSON 上限：tool_result 会整段进一行，默认 64KB 必炸
_LINE_LIMIT = 10 * 1024 * 1024

# stderr 只留尾部这么多字节供错误报告
_STDERR_TAIL = 8192


def _state_path() -> Path:
    return GLOBAL_CONFIG_DIR / "channels" / "relay.json"


def load_all() -> dict[str, dict]:
    """读取全部直连绑定，缺失或损坏返回空字典。"""
    return load_sidecar(_state_path())


def binding_of(thread_id: str) -> dict:
    """某会话的直连绑定（{active, cwd, session_id, model, effort}），无则空。"""
    return load_all().get(thread_id, {})


def is_active(thread_id: str) -> bool:
    """该会话是否处于直连模式。落盘持久化——serve 重启后模式不静默失效。"""
    return bool(binding_of(thread_id).get("active"))


def update_binding(thread_id: str, **fields) -> dict:
    """合并更新某会话的直连绑定；空值删键——``active`` 键存在且为真 = 直连中，故退出
    直连即 ``update_binding(tid, active=False)``（键被清掉），session_id 仍保留供续接。
    """
    return update_sidecar(_state_path(), thread_id, **fields)


# 旗标前缀「短横」的各种形态：中文输入法会把连打的 `--` 自动变成破折号（— U+2014），
# 移动端尤其如此，用户几乎必然踩到。不认它的代价不是少切一个参数，而是整段
# `—dir /path` 被当任务喂给 cc——用户以为切了目录，cc 收到一句没头没尾的话。
# 覆盖 ASCII 短横、U+2010–U+2015 各式连字符/破折号、U+2212 减号、U+FF0D 全角短横。
_FLAG_DASH = re.compile(r"[-\u2010-\u2015\u2212\uff0d]{1,2}(?=[a-z])", re.IGNORECASE)


# --effort 合法档位。cc 对无效值静默接受（实测 --effort bogus 照常跑），必须本侧校验；
# --model 则由 cc 自己报明确错误（"may not exist or you may not have access"），不重复校验。
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
DIRECT_FLAGS = ("dir", "model", "effort")


def parse_direct_args(rest: str) -> tuple[dict[str, str], str]:
    """/direct 进入参数：首行以旗标前缀开头即整行是旗标行，任务从下一行开始。

    旗标行按前缀（``--`` 及其破折号变体，见 ``_FLAG_DASH``）切段，每段 ``key value…``：
    值一直取到下一个前缀为止，故 ``--dir ~/My Projects --model opus`` 与
    ``--model opus --dir ~/My Projects`` 都能解，路径含空格安全。刻意不用引号/包裹类
    语法——输入法弯直混打、忘闭合会静默错切。返回 (旗标 dict, 任务)：未知旗标 / 旗标
    缺值 → 抛 ValueError 带人话原因（调用方响亮报错）；首行不以前缀开头则整段是任务。
    """
    first_line, _, task = rest.partition("\n")
    if not _FLAG_DASH.match(first_line):
        return {}, rest.strip()
    flags: dict[str, str] = {}
    for seg in _FLAG_DASH.split(first_line)[1:]:
        seg = seg.strip()
        if not seg:
            continue
        key, _, value = seg.partition(" ")
        key, value = key.strip(), value.strip()
        if key not in DIRECT_FLAGS:
            raise ValueError(
                f"不认识的选项 `--{key}`（可用：--dir / --model / --effort）"
            )
        if not value:
            raise ValueError(f"`--{key}` 后面缺少值")
        if key == "effort" and value not in EFFORT_LEVELS:
            raise ValueError(f"`--effort` 只能是 {' / '.join(EFFORT_LEVELS)}")
        flags[key] = value
    return flags, task.strip()


def relay_precheck() -> str:
    """直连前置检查，返回不可用原因（空串 = 可用）。

    两条都在进入直连时就拦住，别等到每轮子进程秒退才在红卡里发现：
    - PATH 里没有 claude；
    - 以 root 运行且未设 IS_SANDBOX=1——claude 拒绝 root 下的 bypassPermissions
      （"cannot be used with root/sudo privileges"），218 这类 root systemd 部署会命中。
    """
    if shutil.which("claude") is None:
        return "本机（serve 所在机器）未安装 claude CLI，或不在 PATH。"
    if (
        hasattr(os, "geteuid")
        and os.geteuid() == 0
        and os.environ.get("IS_SANDBOX") != "1"
    ):
        return (
            "serve 以 root 运行，claude 拒绝 root 下免审批执行。"
            "请改用普通用户运行 serve，或在服务环境里设置 IS_SANDBOX=1。"
        )
    return ""


@dataclass(frozen=True)
class RelayEvent:
    """cc 无头轮的中立事件：渠道侧据此驱动流式卡片，不接触 stream-json 原始形状。"""

    kind: str  # "init" | "delta" | "tool_start" | "tool_end" | "result"
    text: str = ""  # delta 正文 / result 的最终文本或错误信息
    name: str = ""  # tool_start / tool_end 的工具名（cc 原名，小写映射由渠道侧做）
    session_id: str = ""  # init / result 携带
    is_error: bool = False


def parse_stream_line(line: str, tool_names: dict[str, str]) -> list[RelayEvent]:
    """一行 stream-json → 零或多个 RelayEvent。纯函数，供测试锁行为。

    tool_names 是调用方持有的 tool_use_id → 工具名映射：tool_use 事件在此登记，
    tool_result 只带 id 不带名，靠它回查。子代理内部活动（parent_tool_use_id 非空）
    一律不外显，与 Lumi 本体的渲染规则一致。
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    if data.get("parent_tool_use_id"):
        return []
    kind = data.get("type")

    if kind == "system" and data.get("subtype") == "init":
        sid = data.get("session_id") or ""
        return [RelayEvent("init", session_id=sid)] if sid else []

    if kind == "stream_event":
        event = data.get("event") or {}
        if event.get("type") == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                return [RelayEvent("delta", text=delta["text"])]
        return []

    if kind == "assistant":
        events: list[RelayEvent] = []
        content = (data.get("message") or {}).get("content") or []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name") or ""
                if block.get("id"):
                    tool_names[block["id"]] = name
                events.append(RelayEvent("tool_start", name=name))
        return events

    if kind == "user":
        events = []
        content = (data.get("message") or {}).get("content") or []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                name = tool_names.pop(block.get("tool_use_id") or "", "")
                if name:
                    events.append(RelayEvent("tool_end", name=name))
        return events

    if kind == "result":
        return [
            RelayEvent(
                "result",
                text=str(data.get("result") or ""),
                session_id=data.get("session_id") or "",
                is_error=bool(data.get("is_error")),
            )
        ]

    return []


async def _drain_stderr(stream: asyncio.StreamReader, tail: bytearray) -> None:
    """后台吸干 stderr（防管道憋死），只留尾部 _STDERR_TAIL 字节供错误报告。"""
    while chunk := await stream.read(4096):
        tail += chunk
        del tail[:-_STDERR_TAIL]


async def run_claude_turn(
    prompt: str, cwd: str, resume_sid: str = "", model: str = "", effort: str = ""
) -> AsyncIterator[RelayEvent]:
    """跑一轮 cc 无头对话，产出 RelayEvent 流。

    权限恒 bypassPermissions：飞书侧无人工审批通道（与渠道「泄漏审批自动拒绝」的
    现状同语义），default/acceptEdits 在 ``-p`` 下会静默拒掉未放行工具，cc 干不了活
    还不知道为什么。prompt 走 stdin（免 argv 长度与转义问题）。

    消费方被取消时经 ``aclose()`` 进入本函数 finally，子进程组随之终止——
    ``/stop`` 的杀伤链即此。进程异常退出且无 result 事件时，合成一个错误 result
    （stderr 尾部作错误文本），调用方无需感知退出码。
    """
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
    ]
    if resume_sid:
        cmd += ["--resume", resume_sid]
    # 模型 / 思考档位是每轮属性而非会话属性：resume 时换模型照样生效且记忆不丢（实测）
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        start_new_session=True,
        limit=_LINE_LIMIT,
    )
    stderr_tail = bytearray()
    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, stderr_tail))
    tool_names: dict[str, str] = {}
    saw_result = False
    try:
        # claude 秒退（未登录 / root 拒绝）时 stdin 会 ConnectionReset：吞掉，让下方
        # "无 result 事件"分支用 stderr 尾部合成可读的错误，而非泛化的"直连轮出错"
        with contextlib.suppress(ConnectionError):
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
        proc.stdin.close()
        while True:
            try:
                raw = await proc.stdout.readline()
            except ValueError:
                # 单行超过 _LINE_LIMIT（超大 tool_result）：丢该事件而非整轮报废；
                # readline 已把越界数据消费掉，下一行继续可读
                logger.warning("claude stream-json 单行超限，已跳过该事件")
                continue
            if not raw:
                break
            for event in parse_stream_line(raw.decode("utf-8", "replace"), tool_names):
                if event.kind == "result":
                    saw_result = True
                yield event
        await proc.wait()
        if not saw_result:
            detail = bytes(stderr_tail).decode("utf-8", "replace").strip()
            logger.error(
                f"claude 无头轮异常退出 code={proc.returncode}: {detail[-500:]}"
            )
            yield RelayEvent(
                "result",
                text=detail.splitlines()[-1] if detail else "Claude Code 进程异常退出",
                is_error=True,
            )
    finally:
        stderr_task.cancel()
        # shield：aclose 途中再遭 cancel 也保证杀完子进程组，不留孤儿 cc 继续跑
        await asyncio.shield(terminate_group(proc))

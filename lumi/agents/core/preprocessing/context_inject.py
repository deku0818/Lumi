"""上下文注入 hook：env / agent / skill / 记忆索引 / LUMI.md 持久注入进末条用户消息。

Claude Code 式持久 reminder：块文本注入进末条 HumanMessage 的 content（进历史、进
checkpoint），``additional_kwargs["ctx_digest"]`` marker 记录「模型已知状态」的条目级
digest。每轮（UserPromptSubmit）比对 marker 与当前状态：

- 无 marker（首轮 / 压缩后——marker 随旧消息一并删除）→ 全量注入；
- 条目变更 → 只注增量 diff（相对上一个 marker，非首次基线的累积 diff）；
  diff 文本比全量长则退化为整块全量；
- 变更源文件被本会话 write/edit 过 → 静默结算（marker 更新、不注通知文本，
  模型自己写的内容无需再告知）；bash 写文件识别不到 → 多通知一次，方向无害；
- 全无变化 → 不注入文本，仅把 marker 前移到末条消息（content 字节不动、缓存
  无损）——保证"写过"名单窗口每轮收口、倒扫恒在上一条用户消息停下。

已知限制（备案不修）：自改静默对 MEMORY.md / LUMI.md 是文件级判定，若模型改过
该文件的同一窗口内还有其它来源的改动（autoDream 在 fork 里写记忆、用户手改
LUMI.md），会一并被静默且不补发。触发需两个写方挤进同一轮窗口，频率低、修复
需跨进程感知，接受此损失。

正确性不变量：历史只被压缩改写，且压缩恒在本 hook **之前**发生（在线 Summarizer
是图首节点、先于 PreprocessMessages；离线 build_compacted_update 在轮外）——压缩
把带 marker 与注入块的消息整体删除，本 hook 永远在压缩后的世界运行，扫不到 marker
即全量重建。故 marker 存在 ⟺ 从上次全量起的完整 diff 链在模型上下文中可见。
缓存收益：变更只动消息尾部，前缀历史缓存不再因写记忆 / 改 skill 而整条作废。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from lumi.agents.core.hooks.schema import HookContext, HookResult
from lumi.agents.core.node_helpers.messages import (
    format_reminder,
    inject_text_into_message,
)
from lumi.agents.core.preprocessing.agent_detector import AgentChangeDetector
from lumi.agents.core.preprocessing.agents import AGENT_HEADER, agent_lines
from lumi.agents.core.preprocessing.memory import (
    MEMORY_HEADER,
    PROJECT_DOC_HEADER,
    memory_index_lines,
    project_doc_lines,
)
from lumi.agents.core.preprocessing.skill_detector import SkillChangeDetector
from lumi.agents.core.preprocessing.skills import SKILL_HEADER, skill_lines
from lumi.agents.core.preprocessing.system_info import system_info_body
from lumi.agents.memory.paths import memory_entrypoint, resolve_under_project
from lumi.agents.memory.project_doc import PROJECT_DOC_NAME
from lumi.agents.permissions.workspace import get_authorized_directory
from lumi.utils.hashing import short_hash

CTX_DIGEST_KEY = "ctx_digest"
"""末条消息 additional_kwargs 中记录「模型已知上下文状态」的 marker 键名。"""

_SELF_EDIT_TOOLS = frozenset({"write", "edit"})

_ANCHOR_MAX_CHARS = 30
"""LUMI.md diff 内容锚的截断长度（锚 = 变更处上方最近的未变行原文）。"""


def _scan_history(messages: list, project_dir: Path) -> tuple[dict | None, set[Path]]:
    """倒扫历史：返回最近的 marker + marker 之后本会话 write/edit 过的路径集合。"""
    written: set[Path] = set()
    for msg in reversed(messages):
        kwargs = getattr(msg, "additional_kwargs", None) or {}
        if CTX_DIGEST_KEY in kwargs:
            return kwargs[CTX_DIGEST_KEY], written
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                if call.get("name") not in _SELF_EDIT_TOOLS:
                    continue
                file_path = (call.get("args") or {}).get("file_path")
                if not file_path:
                    continue
                try:
                    written.add(resolve_under_project(str(file_path), project_dir))
                except (OSError, ValueError):
                    # 模型生成的非法路径（null 字节等）——resolve 抛错若穿透会被
                    # dispatch 吞掉导致整轮注入失效且每轮复现，跳过该条即可
                    continue
    return None, written


def _full_block(header: str, lines: list[str]) -> str:
    return format_reminder(header, lines) if lines else ""


def _prefer_shorter(diff: str, full: str) -> str:
    """增量 diff 与全量重发取更短者（全量为空时恒用 diff）——退化判定的单一口径。"""
    return full if full and len(full) < len(diff) else diff


def _emit_keyed(
    label: str,
    full_header: str,
    entries: dict[str, str],
    old: dict[str, str] | None,
    written: set[Path],
    sources: dict[str, Path] | None = None,
) -> tuple[str, dict[str, str]]:
    """keyed 条目块 → (注入文本, 新 digest)。无变化/全静默返回空文本。

    首次（无 marker）用 ``full_header`` 发全量；变更时按新增/更新/移除分节发增量，
    增量比全量长则退化为「{label}有更新，以下为完整最新列表:」的全量重发。
    """
    digests = {key: short_hash(line) for key, line in entries.items()}
    if old is None:
        return _full_block(full_header, list(entries.values())), digests
    sources = sources or {}
    added, updated = [], []
    for key, line in entries.items():
        if old.get(key) == digests[key] or sources.get(key) in written:
            continue
        (updated if key in old else added).append(line)
    removed = [f"- {key}" for key in old if key not in digests]
    sections: list[str] = []
    for title, lines in (("新增:", added), ("更新:", updated), ("移除:", removed)):
        if lines:
            sections += [title, *lines]
    if not sections:
        return "", digests
    diff = format_reminder(f"{label}有更新:", sections)
    full = _full_block(f"{label}有更新，以下为完整最新列表:", list(entries.values()))
    return _prefer_shorter(diff, full), digests


def _anchor_text(text: str) -> str:
    if len(text) > _ANCHOR_MAX_CHARS:
        text = text[:_ANCHOR_MAX_CHARS] + "…"
    return f"「{text}」之后"


def _line_span(i1: int, i2: int) -> str:
    return f"原第 {i1 + 1}-{i2} 行" if i2 - i1 > 1 else f"原第 {i1 + 1} 行"


def _doc_diff(old: list[str], lines: list[str], hashes: list[str]) -> list[str]:
    """LUMI.md 行级 diff：内容锚（变更处上方最近的未变行原文）+ 行号定位。

    被删行只有 hash 无原文——旧文本模型在历史里可见，锚 + 行号足以定位；
    锚原文从当前文件取（未变行两侧一致）。
    """
    chunks: list[str] = []
    anchor = ""
    matcher = SequenceMatcher(None, old, hashes, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            nonblank = [line for line in lines[j1:j2] if line.strip()]
            anchor = nonblank[-1] if nonblank else anchor
            continue
        where = _anchor_text(anchor) if anchor else "文档开头"
        if tag == "delete":
            chunks.append(f"- {where}的{_line_span(i1, i2)}已删除")
        elif tag == "replace":
            chunks.append(
                f"- {where}（{_line_span(i1, i2)}）更新为:\n" + "\n".join(lines[j1:j2])
            )
        else:  # insert
            chunks.append(f"- {where}新增:\n" + "\n".join(lines[j1:j2]))
    return chunks


def _emit_doc(
    lines: list[str], old: list[str] | None, silenced: bool
) -> tuple[str, list[str]]:
    """LUMI.md 块 → (注入文本, 新行 hash 列表)。"""
    hashes = [short_hash(line) for line in lines]
    if old is None:
        return _full_block(PROJECT_DOC_HEADER, lines), hashes
    if hashes == old or silenced:
        return "", hashes
    diff = format_reminder(
        f"{PROJECT_DOC_NAME} 内容有更新:", _doc_diff(old, lines, hashes)
    )
    full = _full_block(f"{PROJECT_DOC_NAME} 内容有更新，以下为完整最新版本:", lines)
    return _prefer_shorter(diff, full), hashes


def _has_agent_tool(tools: list) -> bool:
    return any(getattr(t, "name", None) == "agent" for t in tools)


def _source_map(configs: list, written: set[Path]) -> dict[str, Path]:
    """条目 name → 定义文件路径（无 path 的条目不参与自改静默）。

    sources 只在与 ``written`` 比对时有用——绝大多数轮 written 为空，
    跳过整批 resolve() 系统调用。
    """
    if not written:
        return {}
    return {c.name: Path(c.path).resolve() for c in configs if c.path}


async def context_inject_hook(ctx: HookContext) -> HookResult:
    """UserPromptSubmit：按 marker 比对注入上下文块（全量 / 增量 / 静默结算）。"""
    runtime = ctx.runtime
    if runtime is None:
        return None
    messages = list(ctx.state.get("messages") or [])
    if not messages or not isinstance(messages[-1], HumanMessage):
        return None

    project_dir = get_authorized_directory()
    old_marker, written = _scan_history(messages, project_dir)
    old = old_marker or {}
    parts: list[str] = []
    marker: dict = {}

    env_body = system_info_body()
    marker["env"] = short_hash(env_body)
    if old.get("env") != marker["env"]:
        header = (
            "用户当前系统环境信息"
            if "env" not in old
            else "环境信息已变更，以下为最新:"
        )
        parts.append(format_reminder(header, [env_body]))

    if _has_agent_tool(runtime.context.tools):
        agents = AgentChangeDetector.get_instance().peek()
        text, marker["agents"] = _emit_keyed(
            "agent 列表",
            AGENT_HEADER,
            agent_lines(agents),
            old.get("agents"),
            written,
            sources=_source_map(agents, written),
        )
        parts.append(text)

    skills = SkillChangeDetector.get_instance().peek()
    text, marker["skills"] = _emit_keyed(
        "技能列表",
        SKILL_HEADER,
        skill_lines(skills),
        old.get("skills"),
        written,
        sources=_source_map(skills, written),
    )
    parts.append(text)

    if runtime.context.memory_enabled:
        # 索引行的变化只能来自 MEMORY.md 本身被写——按文件级判定整块自改静默；
        # 与 _source_map 同口径：written 为空时跳过 resolve
        entries = memory_index_lines(project_dir)
        text, marker["memory"] = _emit_keyed(
            "持久记忆索引",
            MEMORY_HEADER,
            entries,
            old.get("memory"),
            written,
            sources=dict.fromkeys(entries, memory_entrypoint(project_dir).resolve())
            if written
            else None,
        )
        parts.append(text)

    doc_path = (project_dir / PROJECT_DOC_NAME).resolve()
    text, marker["lumi_doc"] = _emit_doc(
        project_doc_lines(project_dir), old.get("lumi_doc"), doc_path in written
    )
    parts.append(text)

    # 无变化也把 marker 前移写到末条（content 字节不动、不破缓存）：
    # ① "本会话写过"名单的窗口每轮收口——防止某次不改 digest 的 write（如改
    #   SKILL.md 正文没动 description）永远留在窗口里、误静默未来的外部变更；
    # ② 倒扫恒在上一条用户消息处停下，长会话不退化为 O(n²)。
    last = messages[-1]
    inject = "".join(parts)
    if inject:
        last = inject_text_into_message(last, inject)
    last = HumanMessage(
        content=last.content,
        additional_kwargs={**last.additional_kwargs, CTX_DIGEST_KEY: marker},
        id=last.id,
    )
    return Command(update={"messages": [last]})

"""context_inject hook 单元测试。

覆盖定稿的行为规则：首轮全量注入 + marker；无变化零注入仅 marker 前移；条目级
增量 diff（相对上一个 marker）；diff 比全量长退化整块；自改静默（write 过的源
文件不通知、marker 照常结算）；LUMI.md 行级 diff；压缩后无 marker 世界全量重建；
末条非 HumanMessage 跳过。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lumi.agents.core.hooks.schema import HookContext
from lumi.agents.core.preprocessing import context_inject
from lumi.agents.core.preprocessing.context_inject import (
    CTX_DIGEST_KEY,
    context_inject_hook,
)


def _cfg(name: str, desc: str, path: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, path=path)


@contextmanager
def _patched(agents=(), skills=(), project_dir=None):
    """mock 加载缓存单例 + 授权目录；project_dir=None 时记忆/LUMI.md 为空。"""
    proj = project_dir or Path("/nonexistent-proj")
    with (
        patch.object(context_inject.AgentChangeDetector, "get_instance") as ai,
        patch.object(context_inject.SkillChangeDetector, "get_instance") as si,
        patch.object(context_inject, "get_authorized_directory", return_value=proj),
    ):
        ai.return_value.peek.return_value = list(agents)
        si.return_value.peek.return_value = list(skills)
        yield


def _runtime(tools=(), memory_enabled=False) -> SimpleNamespace:
    return SimpleNamespace(
        context=SimpleNamespace(tools=list(tools), memory_enabled=memory_enabled)
    )


async def _run(messages: list, runtime=None):
    ctx = HookContext(
        state={"messages": messages},
        config={},
        event="UserPromptSubmit",
        runtime=runtime or _runtime(),
    )
    return await context_inject_hook(ctx)


def _apply(messages: list, cmd) -> list:
    """模拟 add_messages 的同 id 替换：hook 返回的末条覆盖原末条。"""
    replaced = cmd.update["messages"][0]
    return [*messages[:-1], replaced]


async def _seeded() -> list:
    """首轮引导：跑一轮全量注入，返回带 marker 的历史（在 _patched 上下文内调用）。"""
    first = [HumanMessage(content="hi", id="m1")]
    return _apply(first, await _run(first))


def _injected_text(msg: HumanMessage) -> str:
    """末条消息里注入的 reminder 文本（无注入时为空串）。"""
    if isinstance(msg.content, str):
        return ""
    return "".join(
        b["text"]
        for b in msg.content
        if isinstance(b, dict) and "<system-reminder>" in b.get("text", "")
    )


# === 首轮全量 ===


async def test_first_turn_full_injection_and_marker():
    with _patched(skills=[_cfg("myskill", "do x")]):
        cmd = await _run([HumanMessage(content="hi", id="m1")])
    msg = cmd.update["messages"][0]
    assert msg.id == "m1"  # 同 id 替换末条
    text = _injected_text(msg)
    assert "用户当前系统环境信息" in text  # env 全量
    assert "- myskill: do x" in text  # skill 全量
    marker = msg.additional_kwargs[CTX_DIGEST_KEY]
    assert set(marker) == {"env", "skills", "lumi_doc"}  # 无 agent 工具 / 记忆关闭
    assert "myskill" in marker["skills"]
    # 原始用户输入保留在注入块之后
    assert msg.content[-1]["text"] == "hi"


async def test_agents_gated_by_agent_tool():
    agent_tool = SimpleNamespace(name="agent")
    agents = [_cfg("zeta", "z"), _cfg("alpha", "a")]
    with _patched(agents=agents):
        cmd = await _run([HumanMessage(content="hi")], _runtime(tools=[agent_tool]))
        text = _injected_text(cmd.update["messages"][0])
        assert text.index("- alpha") < text.index("- zeta")  # 按名排序
        cmd_no_tool = await _run([HumanMessage(content="hi")])
    assert "alpha" not in _injected_text(cmd_no_tool.update["messages"][0])


# === 无变化：零注入，仅 marker 前移 ===


async def test_no_change_moves_marker_without_injection():
    with _patched(skills=[_cfg("s", "d")]):
        messages = [HumanMessage(content="hi", id="m1")]
        messages = _apply(messages, await _run(messages))
        messages.append(AIMessage(content="ok"))
        messages.append(HumanMessage(content="next", id="m2"))
        cmd = await _run(messages)
    msg = cmd.update["messages"][0]
    assert msg.content == "next"  # content 字节不动（不破缓存）
    assert msg.id == "m2"  # marker 前移到末条——倒扫窗口每轮收口
    assert CTX_DIGEST_KEY in msg.additional_kwargs


async def test_stale_write_does_not_silence_later_external_change(tmp_path):
    """不改 digest 的 write（如改 SKILL.md 正文）随 marker 前移滑出窗口，
    之后的外部变更必须正常通知——盲区 B 回归。"""
    skill_file = tmp_path / "s" / "SKILL.md"
    skills_v1 = [_cfg("s", "描述", path=str(skill_file))]
    with _patched(skills=skills_v1):
        messages = await _seeded()
        # 轮 2：模型 write 该文件但列表行未变（改正文）→ 无注入，marker 前移
        messages.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write",
                        "args": {"file_path": str(skill_file)},
                        "id": "t1",
                    }
                ],
            )
        )
        messages.append(HumanMessage(content="next", id="m2"))
        messages = _apply(messages, await _run(messages))
    # 轮 3：外部改了 description → write 已滑出窗口，不得被误静默
    messages.append(HumanMessage(content="again", id="m3"))
    with _patched(skills=[_cfg("s", "外部改的新描述", path=str(skill_file))]):
        cmd = await _run(messages)
    assert "外部改的新描述" in _injected_text(cmd.update["messages"][0])


# === 条目级增量 diff ===


async def test_incremental_diff_only_new_entry():
    long_desc = "一段足够长的描述，保证全量块比单条 diff 长" * 3
    s1 = _cfg("aaa", long_desc)
    with _patched(skills=[s1]):
        messages = await _seeded()
    messages.append(HumanMessage(content="next", id="m2"))
    s2 = _cfg("bbb", "新技能")
    with _patched(skills=[s1, s2]):
        cmd = await _run(messages)
    text = _injected_text(cmd.update["messages"][0])
    assert "技能列表有更新:" in text
    assert "新增:" in text and "- bbb: 新技能" in text
    assert long_desc not in text  # 未变条目不重发
    marker = cmd.update["messages"][0].additional_kwargs[CTX_DIGEST_KEY]
    assert set(marker["skills"]) == {"aaa", "bbb"}  # marker 永远是全量 digests


async def test_incremental_diff_removed_entry():
    long_desc = "一段足够长的描述，保证全量块比单条 diff 长" * 3
    with _patched(skills=[_cfg("aaa", long_desc), _cfg("bbb", long_desc)]):
        messages = await _seeded()
    messages.append(HumanMessage(content="next", id="m2"))
    with _patched(skills=[_cfg("aaa", long_desc)]):
        cmd = await _run(messages)
    text = _injected_text(cmd.update["messages"][0])
    assert "移除:" in text and "- bbb" in text
    assert (
        "bbb"
        not in cmd.update["messages"][0].additional_kwargs[CTX_DIGEST_KEY]["skills"]
    )


async def test_diff_longer_than_full_falls_back_to_full():
    """大部分条目被移除：diff（多条移除行）比全量（引导行 + 剩余 1 行）长 → 发全量。"""
    removed = [_cfg(f"very_long_skill_name_{i:02d}", "d") for i in range(4)]
    with _patched(skills=[_cfg("s", "简述"), *removed]):
        messages = await _seeded()
    messages.append(HumanMessage(content="next", id="m2"))
    with _patched(skills=[_cfg("s", "简述")]):
        cmd = await _run(messages)
    text = _injected_text(cmd.update["messages"][0])
    assert "技能列表有更新，以下为完整最新列表:" in text  # 退化为整块全量（带引导行）
    assert "- s: 简述" in text
    assert "移除:" not in text


# === 自改静默 ===


async def test_self_written_entry_settles_silently(tmp_path):
    skill_file = tmp_path / "s" / "SKILL.md"
    with _patched(skills=[_cfg("s", "v1", path=str(skill_file))]):
        messages = await _seeded()
        messages.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write",
                        "args": {"file_path": str(skill_file)},
                        "id": "t1",
                    }
                ],
            )
        )
        messages.append(HumanMessage(content="next", id="m2"))
        with patch.object(context_inject.SkillChangeDetector, "get_instance") as si:
            si.return_value.peek.return_value = [_cfg("s", "v2", path=str(skill_file))]
            cmd = await _run(messages)
    msg = cmd.update["messages"][0]
    assert msg.content == "next"  # 无注入文本（模型自己写的）
    assert "s" in msg.additional_kwargs[CTX_DIGEST_KEY]["skills"]  # marker 照常结算
    # 结算后再跑一轮：digest 已一致 → 仍无注入文本，marker 继续前移
    messages = _apply(messages, cmd)
    messages.append(HumanMessage(content="again", id="m3"))
    with _patched(skills=[_cfg("s", "v2", path=str(skill_file))]):
        again = await _run(messages)
    assert again.update["messages"][0].content == "again"


# === LUMI.md 行级 diff ===


async def test_project_doc_line_diff(tmp_path):
    doc = tmp_path / "LUMI.md"
    stable = [f"第{i}节：足够长的项目约定说明，撑起全量块的体积" for i in range(10)]
    doc.write_text("\n".join(["第一行", *stable]), encoding="utf-8")
    with _patched(project_dir=tmp_path):
        first = await _run([HumanMessage(content="hi", id="m1")])
        assert "第一行" in _injected_text(first.update["messages"][0])
        messages = _apply([HumanMessage(content="hi", id="m1")], first)
        messages.append(HumanMessage(content="next", id="m2"))
        doc.write_text("\n".join(["改动的第一行", *stable]), encoding="utf-8")
        cmd = await _run(messages)
    text = _injected_text(cmd.update["messages"][0])
    assert "LUMI.md 内容有更新:" in text
    assert "改动的第一行" in text
    # 文档开头变更无内容锚 → 用「文档开头」+ 行号定位
    assert "文档开头（原第 1 行）更新为:" in text
    assert stable[0] not in text  # 未变行不重发


async def test_project_doc_diff_anchors_to_preceding_line(tmp_path):
    """中间行变更：锚 = 变更处上方最近的未变行原文（截断）+ 行号。"""
    doc = tmp_path / "LUMI.md"
    lines = [f"第{i}节：足够长的项目约定说明，撑起全量块的体积" for i in range(10)]
    doc.write_text("\n".join(lines), encoding="utf-8")
    with _patched(project_dir=tmp_path):
        first = await _run([HumanMessage(content="hi", id="m1")])
        messages = _apply([HumanMessage(content="hi", id="m1")], first)
        messages.append(HumanMessage(content="next", id="m2"))
        doc.write_text(
            "\n".join([*lines[:5], "插入的新行", *lines[5:]]), encoding="utf-8"
        )
        cmd = await _run(messages)
    text = _injected_text(cmd.update["messages"][0])
    assert "插入的新行" in text
    assert f"「{lines[4]}」之后新增:" in text  # 上方最近的未变行作锚


# === 记忆索引门控 ===


async def test_memory_gated_by_memory_enabled(tmp_path, monkeypatch):
    from lumi.agents.memory import ensure_memory_dir, memory_entrypoint
    from lumi.agents.memory import paths as memory_paths

    monkeypatch.setattr(memory_paths, "MEMORY_ROOT", tmp_path / "mem")
    proj = tmp_path / "proj"
    proj.mkdir()
    ensure_memory_dir(proj)
    memory_entrypoint(proj).write_text(
        "- [角色](u.md) — 后端工程师\n", encoding="utf-8"
    )

    with _patched(project_dir=proj):
        on = await _run([HumanMessage(content="hi")], _runtime(memory_enabled=True))
        off = await _run([HumanMessage(content="hi")])
    assert "后端工程师" in _injected_text(on.update["messages"][0])
    assert "后端工程师" not in _injected_text(off.update["messages"][0])
    assert "memory" not in off.update["messages"][0].additional_kwargs[CTX_DIGEST_KEY]


# === 压缩后形态：hook 在无 marker 的世界全量重建 ===


async def test_full_reinjection_after_compaction():
    """压缩后历史 = [Human(<summary>), Human(用户)]（marker 随旧消息删除）→
    hook 对末条注入全量，carrier 不受影响。"""
    from lumi.agents.core.preprocessing.summary import format_summary_block

    carrier = HumanMessage(content=format_summary_block("摘要"), id="c1")
    with _patched(skills=[_cfg("s", "d")]):
        cmd = await _run([carrier, HumanMessage(content="hi", id="m2")])
    msg = cmd.update["messages"][0]
    assert msg.id == "m2"  # 注入到用户消息，不碰 carrier
    text = _injected_text(msg)
    assert "用户当前系统环境信息" in text and "- s: d" in text  # 全量
    assert CTX_DIGEST_KEY in msg.additional_kwargs


# === 防御路径 ===


async def test_skips_when_last_not_human():
    with _patched():
        assert await _run([HumanMessage(content="hi"), AIMessage(content="a")]) is None
        assert await _run([]) is None


async def test_skips_without_runtime():
    ctx = HookContext(
        state={"messages": [HumanMessage(content="hi")]},
        config={},
        event="UserPromptSubmit",
        runtime=None,
    )
    assert await context_inject_hook(ctx) is None


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

"""build_skill_command_blocks 纯函数断言（TUI / desktop 共用的技能命令消息格式）。"""

from __future__ import annotations

from lumi.gateway.bridge import build_skill_command_blocks


def test_blocks_without_extra_text():
    blocks = build_skill_command_blocks("review", "审查代码")
    assert blocks == [
        {
            "type": "text",
            "text": "<command-name>/review</command-name><command-type>skill</command-type>",
        },
        {"type": "text", "text": "<skill-content>审查代码</skill-content>"},
    ]


def test_blocks_with_extra_text_appends_user_input():
    blocks = build_skill_command_blocks("review", "审查代码", "聚焦安全")
    assert blocks[-1] == {"type": "text", "text": "<user-input>聚焦安全</user-input>"}
    assert len(blocks) == 3


async def test_slash_skill_attaches_resource_dir_and_user_input_once(tmp_path):
    # 回归：/技能 注入的只有 skill.prompt 原文——没有资源目录 Tips（技能里「见本目录
    # references/」对安装包内的内置技能无从找起），且追加文本既拼进正文又进 <user-input>，
    # 发了两遍
    from types import SimpleNamespace
    from unittest.mock import patch

    from lumi.agents.tools.loader import SkillConfig
    from lumi.gateway.bridge import AgentBridge

    skill_dir = tmp_path / "probe"
    cfg = SkillConfig(
        name="probe", description="d", prompt="正文", path=str(skill_dir / "SKILL.md")
    )
    bridge = AgentBridge()
    captured: dict = {}

    async def fake_stream_response(blocks, **kw):
        captured["blocks"] = blocks
        return
        yield

    with (
        patch(
            "lumi.agents.core.preprocessing.skill_detector.SkillChangeDetector.get_instance",
            return_value=SimpleNamespace(peek=lambda: [cfg]),
        ),
        patch.object(bridge, "stream_response", fake_stream_response),
    ):
        async for _ in bridge.stream_command("probe", "看看"):
            pass
    text = "".join(b["text"] for b in captured["blocks"])
    assert str(skill_dir) in text
    assert text.count("看看") == 1

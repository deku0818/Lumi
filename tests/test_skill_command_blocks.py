"""build_skill_command_blocks 纯函数断言（TUI / desktop 共用的技能命令消息格式）。"""

from __future__ import annotations

from lumi.agents.bridge import build_skill_command_blocks


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

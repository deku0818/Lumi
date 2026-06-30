"""extract_messages_as_text 扁平 text 导出测试。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from lumi.sessions.message_text import extract_messages_as_text


def test_roles_and_tags():
    msgs = [
        SystemMessage("sys"),
        HumanMessage("你好"),
        AIMessage("好的", tool_calls=[{"name": "bash", "args": {}, "id": "1"}]),
        ToolMessage("done", tool_call_id="1", name="bash"),
        AIMessage("完成"),
    ]
    out = extract_messages_as_text(msgs)
    lines = out.split("\n")
    assert lines[0] == "[user] 你好"  # system 被跳过
    assert lines[1] == "[assistant→tool:bash] 好的"
    assert lines[2] == "[tool:bash] done"
    assert lines[3] == "[assistant] 完成"


def test_newline_folded():
    out = extract_messages_as_text([HumanMessage("第一行\n第二行")])
    assert out == "[user] 第一行⏎第二行"  # 换行折叠为 ⏎，保证一行一消息


def test_empty():
    assert extract_messages_as_text([]) == ""
    assert extract_messages_as_text([SystemMessage("x")]) == ""  # 仅 system → 空

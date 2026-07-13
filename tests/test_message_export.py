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
    assert lines[1] == "[assistant→tool:bash] bash 好的"  # 空 args 只留名字
    assert lines[2] == "[tool:bash] done"
    assert lines[3] == "[assistant] 完成"


def test_tool_call_args_preserved():
    """工具调用参数完整保留（含换行的 content 经 JSON 转义天然单行）。"""
    msgs = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write",
                    "args": {"file_path": "a.py", "content": "第一行\n第二行"},
                    "id": "1",
                }
            ],
        ),
    ]
    out = extract_messages_as_text(msgs)
    assert out.startswith("[assistant→tool:write] write(")
    assert '"file_path": "a.py"' in out
    assert "第一行\\n第二行" in out  # JSON 转义，仍是单行
    assert "\n" not in out


def test_newline_folded():
    out = extract_messages_as_text([HumanMessage("第一行\n第二行")])
    assert out == "[user] 第一行⏎第二行"  # 换行折叠为 ⏎，保证一行一消息


def test_empty():
    assert extract_messages_as_text([]) == ""
    assert extract_messages_as_text([SystemMessage("x")]) == ""  # 仅 system → 空

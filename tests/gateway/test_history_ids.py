from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from lumi.gateway.session import _history_items


def test_ids_survive_history_insertions_and_output_changes():
    human = HumanMessage(
        id="human",
        content="merged",
        additional_kwargs={"lumi": {"items": [{"text": "a"}, {"text": "b"}]}},
    )
    ai = AIMessage(
        id="ai",
        content="reply",
        tool_calls=[{"id": "call", "name": "read", "args": {}}],
    )
    before = _history_items([human, ai])
    after = _history_items(
        [
            HumanMessage(id="new", content="inserted"),
            human,
            ai,
            ToolMessage(id="result", tool_call_id="call", content="done"),
        ]
    )
    assert [i["id"] for i in before] == [i["id"] for i in after[1:]]
    assert len({i["id"] for i in before}) == len(before) == 4
    assert after[-1]["output"] == "done"
    assert before[0]["message_id"] == before[1]["message_id"] == "human"


def test_legacy_missing_ids_are_stable_and_unique():
    messages = [
        HumanMessage(content="same"),
        HumanMessage(content="same"),
        AIMessage(content="reply"),
    ]
    before = _history_items(messages)
    after = _history_items([HumanMessage(content="new"), *messages])
    assert [i["id"] for i in before] == [i["id"] for i in after[1:]]
    assert len({i["id"] for i in before}) == 3

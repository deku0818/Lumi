"""titler 素材提取纯逻辑测试（不触 LLM）。"""

from langchain_core.messages import AIMessage, HumanMessage

from lumi.agents.core.meta_message import meta_human_message
from lumi.gateway.titler import _TAIL_CHARS, refresh_digest


def test_refresh_digest_below_threshold_returns_empty():
    # 快照 1 条可见用户消息 + 当前第 2 条 → 未到第 3 条，不刷新
    msgs = [HumanMessage(content="第一问"), AIMessage(content="第一答")]
    assert refresh_digest(msgs, "第二问") == ""


def test_refresh_digest_appends_current_when_not_in_snapshot():
    # 快照 2 条可见用户消息，当前是尚未落 checkpoint 的第 3 条 → 刷新，素材含它
    msgs = [
        HumanMessage(content="第一问"),
        AIMessage(content="第一答"),
        HumanMessage(content="第二问"),
        AIMessage(content="第二答"),
    ]
    digest = refresh_digest(msgs, "第三问")
    assert digest.endswith("第三问")
    assert "第一答" in digest and "第二问" in digest


def test_refresh_digest_repeated_text_still_counts():
    # 当前消息与上一条文本相同（如连发两次「继续」）也照常计数——
    # 按文本判重会把它误判为已入快照，悄悄推迟第 3 条的定稿刷新
    msgs = [
        HumanMessage(content="第一问"),
        AIMessage(content="第一答"),
        HumanMessage(content="继续"),
    ]
    assert refresh_digest(msgs, "继续") != ""


def test_refresh_digest_meta_not_counted():
    # meta 注入消息不计入可见用户消息：1 条真实 + 2 条 meta + 当前 = 2 条，不刷新
    msgs = [
        HumanMessage(content="第一问"),
        meta_human_message("后台任务通知"),
        meta_human_message("又一条通知"),
    ]
    assert refresh_digest(msgs, "第二问") == ""


def test_refresh_digest_tail_slices_and_excludes_meta_text():
    # 素材只保留末尾 _TAIL_CHARS 字符（近期话题优先），meta 文本不进素材
    msgs = [
        HumanMessage(content="早" * 3000),
        meta_human_message("后台任务通知"),
        HumanMessage(content="中"),
        AIMessage(content="答" * 500),
    ]
    digest = refresh_digest(msgs, "尾")
    assert digest != ""
    assert len(digest) <= _TAIL_CHARS
    assert digest.endswith("尾")
    assert "通知" not in digest

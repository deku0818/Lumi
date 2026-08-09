"""合成消息 + 显示声明契约。

每条 HumanMessage 在构造时经 ``additional_kwargs["lumi"]["items"]`` 声明自己的
显示（气泡条目列表），content 只给模型看：

- ``items`` 非空 → 按条目渲染（text / sender / ts / files）；
- ``items: []`` → 声明"无可显示"——摘要 carrier、后台任务通知、read 工具的
  图片/PDF 回灌、hook reminder 等合成消息，经 ``synthetic_human_message()`` /
  ``reminder_human_message()`` 构造；
- 未声明（cron / 子 agent / workflow / dream 直接构造的消息）→ 显示侧 fallback：
  content 掉 ``injected_prefix`` 前缀块后取文本（那些路径的 content 本就无标签）。

``is_hook_reminder`` 是独立的**图语义**标记（轮内合成插话、非轮边界），与显示
无关：后台通知和 reminder 在显示上都不可见，但前者是轮边界后者不是，
``iter_current_turn`` 靠它区分。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from lumi.utils.constants import LUMI_META_KEY

INJECTED_PREFIX_KEY = "injected_prefix"
"""标记 content 前 N 个 block 为注入块（非用户输入）的键名。

注入恒为**前置**（``inject_text_into_message`` 插 index 0），故注入块天然构成
content 前缀。未声明 items 的消息（cron / 子 agent）显示侧按此计数整块丢弃；
计数放 additional_kwargs 而非 block 自定义字段：langchain_openai 对 text block
原样透传，多余字段会直达 provider API。"""

CTX_DIGEST_KEY = "ctx_digest"
"""记录「模型已知上下文状态」的 marker 键名（写侧见 ``preprocessing.context_inject``）。

与 items / injected_prefix 同放：消息元数据键的单一真源。注入侧写 marker、压缩侧
（``preprocessing.compact``）剥 marker，两侧都从这里取键名、不互相 import。"""

REMINDER_KEY = "is_hook_reminder"
"""标记"hook 注入的 system-reminder"的键名。

reminder 是**轮内合成插话、不是轮边界**——区别于后台任务通知等真实合成消息
（它们是模型要响应的新输入，构成轮边界）。结构化输出的连续失败计数 / 拉回计数 /
accepted 判定都靠 ``is_reminder_message`` 精确跳过 reminder，否则后台通知会被
误当成 reminder 跳过、导致跨轮泄漏计数。"""


def synthetic_human_message(
    content: str | list[dict[str, Any]], *, ts: int = 0
) -> HumanMessage:
    """构造合成 HumanMessage：声明 ``items: []``（给模型看、无可显示）。

    ``ts``（毫秒）仅摘要 carrier 传：压缩把真人消息删光时由它继承那条消息的落库时刻，
    见 ``build_summary_carrier``。0 = 不写该键（``message_ts`` 缺键即 0，语义一致）。
    """
    meta: dict[str, Any] = {"items": []}
    if ts:
        meta["ts"] = ts
    return HumanMessage(content=content, additional_kwargs={LUMI_META_KEY: meta})


def reminder_human_message(content: str | list[dict[str, Any]]) -> HumanMessage:
    """构造 hook 注入的 system-reminder：无可显示 + 图侧 reminder 标记。"""
    return HumanMessage(
        content=content,
        additional_kwargs={LUMI_META_KEY: {"items": []}, REMINDER_KEY: True},
    )


def _additional_kwargs(msg: object) -> dict:
    if isinstance(msg, dict):
        return msg.get("additional_kwargs") or {}
    return getattr(msg, "additional_kwargs", None) or {}


def declared_items(msg: object) -> list[dict] | None:
    """消息声明的显示条目；未声明（无 items 键）返回 None。"""
    meta = _additional_kwargs(msg).get(LUMI_META_KEY)
    if isinstance(meta, dict) and "items" in meta:
        return meta["items"]
    return None


def message_ts(msg: object) -> int:
    """消息的落库时刻（毫秒），未标记返回 0。

    只有真实用户消息（bridge 的 ``_build_user_message``）与继承其时刻的摘要
    carrier 带 ts，其余合成消息一律没有——故「带 ts」即「此处有过真人输入」。
    """
    meta = _additional_kwargs(msg).get(LUMI_META_KEY)
    return meta.get("ts", 0) if isinstance(meta, dict) else 0


def injected_prefix(msg: object) -> int:
    """消息 content 开头有几个注入块（无标记返回 0）。"""
    return _additional_kwargs(msg).get(INJECTED_PREFIX_KEY, 0)


def strip_injected_prefix(msg: object) -> str | list:
    """消息 content 掉掉注入块前缀，返回「用户原样输入」的 content。

    注入恒为前置（``inject_text_into_message`` 插 index 0），故按计数切片即可。
    显示侧（``visible_user_text`` 的 fallback）与重发侧（重建干净消息）共用——
    「前 N 块是注入」这条不变量只此一处解读。
    """
    content = (
        msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
    )
    skip = injected_prefix(msg)
    return content[skip:] if skip and isinstance(content, list) else content


def declared_file_paths(msg: object) -> list[str]:
    """显示声明里的附件后端路径（重发时按声明重挂 ``<attached-file>`` 标签）。

    与 ``declared_items`` 同处：``items[].files[].path`` 的形状契约只此一处解读。
    """
    return [
        f["path"]
        for it in declared_items(msg) or []
        for f in it.get("files") or []
        if f.get("path")
    ]


def strip_ctx_digest(msg: BaseMessage) -> BaseMessage:
    """剥掉 ctx_digest marker，返回新消息（无 marker 时原对象直接返回）。

    marker 不变量是「存在 ⟺ 从上次全量起的完整 diff 链可见」——凡删除 marker 之后
    的历史（压缩重挂 / 时间旅行截断），保留侧消息必须经此剥离，否则 context_inject
    误判「已注入过」而漏注上下文。压缩/截断/重发三处调用点共用，键名契约单源在此。
    """
    if CTX_DIGEST_KEY not in msg.additional_kwargs:
        return msg
    kwargs = {k: v for k, v in msg.additional_kwargs.items() if k != CTX_DIGEST_KEY}
    return msg.model_copy(update={"additional_kwargs": kwargs})


def is_reminder_message(msg: object) -> bool:
    """判断是否为 hook 注入的 system-reminder（轮内合成插话，不是轮边界）。"""
    return bool(_additional_kwargs(msg).get(REMINDER_KEY))


def iter_current_turn(messages: list) -> Iterator[Any]:
    """从尾部 yield 本轮消息（新→旧），到第一条**真实** HumanMessage 为止（不含它）。

    hook reminder（``is_hook_reminder``）是轮内合成插话——跳过（仍 yield）继续上溯；
    后台任务通知等真实合成消息是模型要响应的新输入、构成轮边界，遇到即停。

    把"本轮窗口"边界判定收在一处，供结构化输出的连续失败计数 / 拉回计数 / accepted
    判定共用——避免各扫描器各自重复倒扫骨架、且漏掉 reminder 跳过导致跨轮泄漏。
    """
    for msg in reversed(messages or []):
        if isinstance(msg, HumanMessage) and not is_reminder_message(msg):
            return
        yield msg

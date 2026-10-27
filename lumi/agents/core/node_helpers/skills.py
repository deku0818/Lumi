"""合并并行技能调用的结果，只缩短新结果，不改历史消息。"""

from __future__ import annotations

from langchain_core.messages import ToolMessage

from lumi.agents.tools.providers.skill import skill_loaded_notice


def deduplicate_skill_results(
    calls: list[dict],
    results: list[ToolMessage],
) -> list[ToolMessage]:
    """只比较本批实际返回的文本，不加载配置或扫描历史。"""
    names = {c["id"]: c["args"].get("name") for c in calls if c["name"] == "skill"}
    seen: set[tuple[str, str]] = set()
    prepared: list[ToolMessage] = []
    for msg in results:
        if (
            msg.name == "skill"
            and msg.status != "error"
            and isinstance(msg.content, str)
        ):
            _, separator, resources = msg.content.rpartition("\n\n<skill-resources>")
            if separator and resources.endswith("</skill-resources>"):
                key = (names[msg.tool_call_id], msg.content)
                if key in seen:
                    content = skill_loaded_notice(
                        key[0], f"<skill-resources>{resources}"
                    )
                    if content != msg.content:
                        msg = msg.model_copy(update={"content": content})
                seen.add(key)
        prepared.append(msg)
    return prepared

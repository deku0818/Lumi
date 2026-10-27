"""Skill 工具与手动命令共用的技能正文渲染与上下文去重。"""

# 注意：本模块**不能**加 `from __future__ import annotations`——它会把
# `runtime: ToolRuntime` 变成字符串注解，LangGraph 的注入识别失效（同 agent.py 的约定，
# 见回归测试 test_runtime_injected_via_toolnode）。
from collections.abc import Sequence
from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from lumi.agents.tools.loader import SkillConfig

_SKILL_DESCRIPTION = """在主对话中执行技能（skill）。

当用户要求执行某项任务时，先检查可用技能里有没有匹配的。技能提供专门的能力与领域知识。

当用户提到"斜杠命令"或 "/<某命令>"（如 "/commit"、"/review-pr"）时，指的就是技能，请用本工具调用它。

如何调用：
- 用本工具并指定技能名称，如 `name: "pdf"` 调用 pdf 技能

注意事项：
- 可用技能列表在对话中的 `<system-reminder>` 里给出
- 用户请求与某技能匹配时，这是强制要求：必须先调用该技能，再生成关于此任务的其它回应
- 不要调用已在运行中的技能
- 不要用本工具执行系统命令（如 /stop、/clear、/help）——它们由渠道层直接处理，不是技能
- 若当前对话回合已出现 `<command-name>` 标签，说明技能已加载——直接按 `<skill-content>` 的指示执行，不要再调用本工具"""


class SkillInput(BaseModel):
    """Skill 工具的输入参数"""

    name: str = Field(description="技能名称")


@tool(description=_SKILL_DESCRIPTION, args_schema=SkillInput)
async def skill(name: str, runtime: ToolRuntime) -> str:
    """根据名称查找并返回对应的技能提示词。"""
    # 走按项目缓存的 detector（与 bridge 的 /命令路径同源），文件未变不重解析
    from lumi.agents.core.preprocessing.skill_detector import SkillChangeDetector

    project_dir = runtime.context.project_dir
    skills = SkillChangeDetector.get_instance(project_dir).peek()
    skill_config: SkillConfig | None = next((s for s in skills if s.name == name), None)
    if skill_config is None:
        return f"技能 '{name}' 不存在，请检查技能名称是否正确"

    return render_skill(skill_config, runtime.state.get("messages", []))


def render_skill(
    skill_config: SkillConfig,
    messages: Sequence[BaseMessage] = (),
) -> str:
    """返回技能全文或已加载提示，两者都附带资源目录标签。"""
    source_dir = Path(skill_config.path).parent if skill_config.path else None
    skill_path = str(source_dir) if source_dir else f"skills/{skill_config.name}"
    resources = (
        f"<skill-resources>技能资源位于 `{skill_path}/` 目录下。</skill-resources>"
    )
    content = f"{skill_config.prompt}\n\n{resources}"
    # 只认可当前消息中实际保留的全文；短提示、摘要和截断结果均不能充当已加载记录。
    # 无独立缓存：会话隔离、重启恢复、时间旅行和压缩自然遵循现有 messages。
    blocks = [
        {"type": "text", "text": f"<command-name>/{skill_config.name}</command-name>"},
        *skill_content_blocks(content),
    ]
    for msg in messages:
        if isinstance(msg, ToolMessage):
            loaded = (
                msg.name == "skill" and msg.status != "error" and msg.content == content
            )
        else:
            loaded = (
                isinstance(msg, HumanMessage)
                and isinstance(msg.content, list)
                and all(block in msg.content for block in blocks)
            )
        if loaded:
            return skill_loaded_notice(skill_config.name, resources)
    return content


def skill_loaded_notice(name: str, resources: str) -> str:
    """普通调用和批内去重共用的短提示。"""
    return (
        f"技能 '{name}' 已在当前上下文中加载且未变化，"
        "请继续遵循此前的完整内容执行本次任务。"
        f"\n\n{resources}"
    )


def skill_content_blocks(content: str) -> list[dict[str, str]]:
    """将 render_skill 的输出拆成同级正文块和资源块。"""
    body, resources = content.rsplit("\n\n<skill-resources>", 1)
    return [
        {"type": "text", "text": f"<skill-content>{body}</skill-content>"},
        {"type": "text", "text": f"<skill-resources>{resources}"},
    ]

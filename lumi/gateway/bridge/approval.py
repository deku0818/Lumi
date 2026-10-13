"""权限审批富化：在 Bridge 层为 tool_approval 请求补充命令告警，
使 Graph 侧保持纯净的三态契约。"""

from __future__ import annotations

from lumi.agents.permissions.matcher import (
    COMMAND_ARG_KEYS,
    COMMAND_TOOLS,
    extract_arg,
)
from lumi.agents.permissions.validators import validate_bash_command


def enrich_tool_approval(data: dict) -> dict:
    """给每个 ``tool_calls[i]`` 补 ``warnings``（非空才带）。

    挂在各自的调用上而非整批摊平：逐个审批时每页只该显示该调用自己的风险。
    只包含需要人工审批的调用；deny 已单独拒绝，allow 已直接执行。
    """
    for tc in data.get("tool_calls", []):
        name = tc.get("name", "")
        args = tc.get("args", {})
        if name in COMMAND_TOOLS:
            command = extract_arg(args, COMMAND_ARG_KEYS) or ""
            if warnings := [
                f"{'⚠' if w.level == 'danger' else '⚡'} {w.message}"
                for w in validate_bash_command(command)
            ]:
                tc["warnings"] = warnings
    return data

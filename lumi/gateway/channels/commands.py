"""渠道侧斜杠命令解析（渠道无关，飞书等 IM 渠道共用）。

IM 没有 desktop 的补全菜单，这里只做纯语法切分；是否为已知命令由调用方对照
``bridge.list_commands()`` 判定，未命中的按普通文本喂模型。
"""

from __future__ import annotations

import re

# IM 渠道通用系统命令：渠道层直接执行，不进 agent、不排队。desktop 有终止/删除
# 按钮，不需要它们，故不进 bridge.list_commands()（否则 desktop 补全里也会冒出来）。
# handler 依赖各渠道的传输层，由渠道自己实现（飞书在 inbound._run_system_command），
# 这里只共享名字与描述——第二个渠道接入时复用同一套命令面。
SYSTEM_COMMANDS: dict[str, str] = {
    "stop": "停止当前正在执行的任务",
    "clear": "清空本会话历史",
    "help": "列出可用命令",
}

# mention 场景下命令起点：第一个前面是空白、以 "/" 开头的片段（到文本末尾）
_MENTION_COMMAND = re.compile(r"\s(/.*)", re.DOTALL)


def parse_slash_command(text: str) -> tuple[str, str] | None:
    """按语法切出斜杠命令：``/name extra`` → ``(name, extra)``，非命令形态返回 None。

    群聊 mention 模式下正文形如 ``@Lumi /commit …``；显示名可含空格（"Lumi Bot"），
    无法按 token 剥名字，故 @ 开头时直接取第一个空白后跟 "/" 的位置作为命令起点。
    是否为已知命令仍由调用方兜底，"@Lumi 看下 /etc/hosts" 这类误切最终按普通文本走。
    """
    stripped = text.strip()
    if stripped.startswith("@"):
        match = _MENTION_COMMAND.search(stripped)
        if match is None:
            return None
        stripped = match.group(1)
    if len(stripped) < 2 or not stripped.startswith("/") or stripped[1].isspace():
        return None
    parts = stripped[1:].split(maxsplit=1)
    return parts[0], (parts[1].strip() if len(parts) > 1 else "")

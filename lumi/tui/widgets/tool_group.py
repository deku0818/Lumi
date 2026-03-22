"""工具组组件 — 将连续的工具调用合并为可折叠的摘要行

连续的工具调用（中间无 assistant 文本打断）收进一个 ToolGroup 容器，
折叠态显示一行灰色摘要（如 "Read 4 files"），展开后显示各 ToolBlock 详情。

摘要格式规则：
- 同工具 + 同文件：  "Edited node.py 6 times"
- 同工具 + 不同文件："Read 4 files"
- 同工具 + 非文件：  "Ran 3 commands"
- 混合工具：         "Performed 6 actions"
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.events import Click
from textual.widgets import Static

from lumi.tui.renderers import get as get_renderer
from lumi.tui.theme import get_color
from lumi.tui.widgets.tool_block import ToolBlock, ToolStatus

logger = logging.getLogger(__name__)


# 不参与合并的工具名（有独立交互流程）
_EXCLUDED_TOOLS: frozenset[str] = frozenset({"ask", "agent"})


@dataclass
class _BlockEntry:
    """ToolGroup 内部追踪的单个工具调用信息。"""

    block: ToolBlock
    tool_name: str
    target: str  # 文件路径或关键参数值，用于摘要生成


def should_exclude_from_group(name: str, approval_mode: bool) -> bool:
    """判断工具调用是否应排除在 ToolGroup 合并之外。

    Args:
        name: 工具名称
        approval_mode: 是否处于审批模式
    """
    return name in _EXCLUDED_TOOLS or approval_mode


def _extract_target(name: str, args: dict) -> str:
    """从工具参数中提取目标标识（文件路径或关键参数值）。

    优先使用渲染器的 group_target_key，回退到 title_arg_key。
    """
    renderer = get_renderer(name)
    key = getattr(renderer, "group_target_key", "")
    if key:
        value = args.get(key, "")
        return str(value) if value else ""
    return ""


def _get_group_attrs(name: str) -> tuple[str, str, str]:
    """获取渲染器的分组属性 (verb, verb_active, noun)。"""
    renderer = get_renderer(name)
    verb = getattr(renderer, "group_verb", "") or ""
    verb_active = getattr(renderer, "group_verb_active", "") or ""
    noun = getattr(renderer, "group_noun", "") or ""
    return verb, verb_active, noun


def _build_summary_text(entries: list[_BlockEntry], *, is_running: bool) -> str:
    """根据 entries 列表生成摘要文本。

    Args:
        entries: 工具调用条目列表
        is_running: 是否有工具仍在执行中
    """
    if not entries:
        return ""

    count = len(entries)
    tool_names = {e.tool_name for e in entries}

    # 单一工具类型
    if len(tool_names) == 1:
        name = entries[0].tool_name
        verb, verb_active, noun = _get_group_attrs(name)
        active_verb = verb_active if is_running else verb

        # 没有配置分组属性的工具，回退到通用格式
        if not active_verb or not noun:
            active_verb = "Performing" if is_running else "Performed"
            noun = "action"

        targets = [e.target for e in entries if e.target]
        unique_targets = list(dict.fromkeys(targets))  # 保序去重

        # 同工具 + 同文件
        if len(unique_targets) == 1:
            filename = os.path.basename(unique_targets[0])
            if count == 1:
                return f"{active_verb} {filename}"
            return f"{active_verb} {filename} {count} times"

        # 同工具 + 不同文件（有文件参数）
        if unique_targets:
            return f"{active_verb} {count} {noun}s"

        # 同工具 + 无文件参数
        if count == 1:
            return f"{active_verb} 1 {noun}"
        return f"{active_verb} {count} {noun}s"

    # 混合工具类型：按工具分别描述，如 "Searched 1 pattern, read 1 file, edited 1 file"
    parts: list[str] = []
    # 按出现顺序分组
    seen_tools: list[str] = []
    tool_counts: dict[str, int] = {}
    for e in entries:
        if e.tool_name not in tool_counts:
            seen_tools.append(e.tool_name)
            tool_counts[e.tool_name] = 0
        tool_counts[e.tool_name] += 1

    for i, name in enumerate(seen_tools):
        cnt = tool_counts[name]
        verb, verb_active, noun = _get_group_attrs(name)
        # 首个工具用大写开头，后续小写
        active_verb = verb_active if is_running else verb
        if not active_verb or not noun:
            active_verb = "performed" if not is_running else "performing"
            noun = "action"
        if i == 0:
            active_verb = active_verb[0].upper() + active_verb[1:]
        else:
            active_verb = active_verb[0].lower() + active_verb[1:]
        plural = f"{noun}s" if cnt > 1 else noun
        parts.append(f"{active_verb} {cnt} {plural}")

    return ", ".join(parts)


class _SummaryLine(Static):
    """可点击的摘要行，点击时切换 ToolGroup 的展开/折叠状态。"""

    DEFAULT_CSS = """
    _SummaryLine {
        padding: 0;
        margin: 0;
        height: auto;
        width: 1fr;
    }
    """

    def on_click(self, event: Click) -> None:
        """点击摘要行时通知父 ToolGroup 切换展开状态。"""
        event.stop()
        parent = self.parent
        if isinstance(parent, ToolGroup):
            parent.toggle_expanded()


class ToolGroup(Vertical):
    """工具组容器 — 将连续工具调用合并为可折叠摘要。

    不使用 Collapsible（避免与内层 ToolBlock 的 Collapsible 嵌套冲突），
    而是用 _SummaryLine + display toggle 实现折叠/展开。

    折叠态：一行灰色摘要文本
    展开态：摘要 + 各 ToolBlock 详情
    """

    DEFAULT_CSS = """
    ToolGroup {
        margin: 0 0 1 0;
        padding: 0 1;
        height: auto;
    }

    ToolGroup .group-blocks {
        height: auto;
        padding: 0 0 0 1;
        margin: 0 0 0 1;
    }

    ToolGroup .group-blocks ToolBlock {
        margin: 0;
        padding: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[_BlockEntry] = []
        self._finalized = False
        self._has_error = False
        self._expanded = False

    def compose(self) -> ComposeResult:
        """组合：摘要行 + blocks 容器（初始隐藏）。"""
        yield _SummaryLine("", markup=False, id=f"group-summary-{id(self)}")
        yield Vertical(classes="group-blocks", id=f"group-blocks-{id(self)}")

    def on_mount(self) -> None:
        """挂载后隐藏 blocks 容器（折叠态）。"""
        try:
            blocks = self.query_one(".group-blocks", Vertical)
            blocks.display = False
        except NoMatches:
            logger.debug("on_mount: group-blocks container not ready")

    def toggle_expanded(self) -> None:
        """切换展开/折叠状态。"""
        self._expanded = not self._expanded
        try:
            blocks = self.query_one(".group-blocks", Vertical)
            blocks.display = self._expanded
        except NoMatches:
            logger.debug("toggle_expanded: group-blocks container not ready")

    async def add_block(self, block: ToolBlock, name: str, args: dict) -> None:
        """追加一个工具调用到组内。

        Args:
            block: 已创建的 ToolBlock 实例
            name: 工具名称
            args: 工具参数
        """
        target = _extract_target(name, args)
        entry = _BlockEntry(block=block, tool_name=name, target=target)
        self._entries.append(entry)

        try:
            blocks_container = self.query_one(".group-blocks", Vertical)
            await blocks_container.mount(block)
        except NoMatches:
            logger.error(
                "add_block failed: container not ready (tool=%s)",
                name,
            )
            return

        # 更新摘要
        self._refresh_summary(is_running=True)

    def _refresh_summary(self, *, is_running: bool) -> None:
        """重新计算并更新摘要标题。"""
        summary = _build_summary_text(self._entries, is_running=is_running)
        if is_running:
            summary += "…"

        title_text = Text()
        title_text.append(summary, style=get_color("text_muted"))

        try:
            summary_line = self.query_one(_SummaryLine)
            summary_line.update(title_text)
        except NoMatches:
            logger.debug("_refresh_summary: SummaryLine not ready")

    def notify_block_done(self, block: ToolBlock) -> None:
        """某个 ToolBlock 完成时调用，更新摘要状态。"""
        if block.status == ToolStatus.ERROR:
            self._has_error = True

        # 检查是否所有 block 都已完成
        all_done = all(
            e.block.status
            in (ToolStatus.DONE, ToolStatus.ERROR, ToolStatus.INTERRUPTED)
            for e in self._entries
        )
        if all_done:
            self._finalize_summary()

    def _finalize_summary(self) -> None:
        """所有工具完成后最终化摘要。"""
        self._finalized = True

        error_count = sum(
            1
            for e in self._entries
            if e.block.status in (ToolStatus.ERROR, ToolStatus.INTERRUPTED)
        )

        summary = _build_summary_text(self._entries, is_running=False)
        if error_count > 0:
            summary += f" ({error_count} failed)"

        title_text = Text()
        if error_count > 0:
            title_text.append(summary, style=get_color("error"))
        else:
            title_text.append(summary, style=get_color("text_muted"))

        try:
            summary_line = self.query_one(_SummaryLine)
            summary_line.update(title_text)

            if error_count > 0 and not self._expanded:
                self.toggle_expanded()
        except NoMatches:
            logger.error(
                "_finalize_summary failed: SummaryLine not found",
            )

    def finalize_group(self) -> None:
        """关闭工具组（遇到文本打断时调用）。"""
        if not self._finalized:
            all_done = all(
                e.block.status
                in (ToolStatus.DONE, ToolStatus.ERROR, ToolStatus.INTERRUPTED)
                for e in self._entries
            )
            if all_done:
                self._finalize_summary()

    @property
    def block_count(self) -> int:
        """当前组内的工具调用数量。"""
        return len(self._entries)

    @property
    def is_finalized(self) -> bool:
        return self._finalized

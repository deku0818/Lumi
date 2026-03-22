"""多 Agent 组合组件 — 轻量摘要模式

运行中：显示各子 agent 的名称、工具使用数、token 数和当前动作
完成后：总摘要 + 各 agent 行始终可见，点击某个 agent 行展开其 prompt + result
不渲染子 agent 内部的 ToolBlock/AssistantMessage，避免 DOM 爆炸。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.events import Click
from textual.timer import Timer
from textual.widgets import Static

from lumi.tui.renderers.utils import BLINK_FRAMES
from lumi.tui.theme import get_color

logger = logging.getLogger(__name__)

# 当前动作描述的最大长度
_ACTION_MAX_LEN = 60


@dataclass
class AgentEntry:
    """单个子 agent 的运行统计。"""

    name: str
    prompt: str
    run_id: str
    tool_uses: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    current_action: str = "Thinking…"
    result: str = ""
    done: bool = False
    error: bool = False
    expanded: bool = False


def _format_tokens(n: int) -> str:
    """格式化 token 数为可读字符串。"""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


class _HeaderLine(Static):
    """总摘要标题行（不可点击）。"""

    DEFAULT_CSS = """
    _HeaderLine {
        height: auto;
        width: 1fr;
        padding: 0;
        margin: 0;
    }
    """


class _AgentLine(Static):
    """单个子 agent 的摘要行，完成后可点击展开 prompt + result。"""

    DEFAULT_CSS = """
    _AgentLine {
        height: auto;
        width: 1fr;
        padding: 0 0 0 2;
        margin: 0;
    }
    """

    def on_click(self, event: Click) -> None:
        """点击时通知 AgentGroup 切换该 agent 的详情展开状态。"""
        event.stop()
        group = self.ancestors_with_self
        for ancestor in group:
            if isinstance(ancestor, AgentGroup):
                # 从 widget id 提取 run_id
                wid = self.id or ""
                prefix = "ag-line-"
                if wid.startswith(prefix):
                    run_id = wid[len(prefix) :]
                    ancestor.toggle_agent_detail(run_id)
                break


class _AgentDetail(Vertical):
    """展开后单个子 agent 的详情（prompt + result）。"""

    DEFAULT_CSS = """
    _AgentDetail {
        height: auto;
        padding: 0 0 0 5;
        margin: 0 0 0 0;
    }
    _AgentDetail > Static {
        margin: 0;
        padding: 0;
    }
    _AgentDetail > .agent-detail-content {
        padding: 0 0 0 2;
    }
    """


class AgentGroup(Vertical):
    """多 Agent 组合容器 — 轻量摘要模式。

    运行中：标题行 + 每个 agent 一行摘要（名称 · 工具数 · token 数 · 当前动作）
    完成后：总摘要 + 各 agent 行始终可见，点击某个 agent 行展开其 prompt + result
    """

    DEFAULT_CSS = """
    AgentGroup {
        margin: 0 0 1 0;
        padding: 0 1;
        height: auto;
    }
    AgentGroup .agent-lines {
        height: auto;
        padding: 0;
        margin: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._entries: dict[str, AgentEntry] = {}  # run_id → AgentEntry
        self._order: list[str] = []  # 保持插入顺序
        self._finalized = False
        self._spinner_frame = 0
        self._spinner_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield _HeaderLine("", markup=False, id=f"ag-header-{id(self)}")
        yield Vertical(classes="agent-lines", id=f"ag-lines-{id(self)}")

    def on_mount(self) -> None:
        """挂载后启动 spinner。"""
        self._spinner_timer = self.set_interval(0.5, self._tick)

    def _tick(self) -> None:
        """定时刷新：更新标题和各 agent 行。"""
        self._spinner_frame += 1
        if not self._finalized:
            self._refresh_header()
            self._refresh_lines()

    # ── 公开 API ──

    def add_agent(self, run_id: str, name: str, prompt: str) -> None:
        """注册一个新的子 agent。"""
        if run_id in self._entries:
            return
        entry = AgentEntry(name=name, prompt=prompt, run_id=run_id)
        self._entries[run_id] = entry
        self._order.append(run_id)
        try:
            container = self.query_one(".agent-lines", Vertical)
            line = _AgentLine("", markup=False, id=f"ag-line-{run_id}")
            self.call_after_refresh(lambda: container.mount(line))
        except NoMatches:
            logger.error(
                "add_agent failed: container not ready (event_type=add_agent, run_id=%s)",
                run_id,
            )
        self._refresh_header()

    def record_tool_start(self, run_id: str, tool_name: str, args: dict) -> None:
        """记录子 agent 的工具调用开始。"""
        entry = self._entries.get(run_id)
        if entry is None:
            logger.debug("record_tool_start: unknown run_id=%s", run_id)
            return
        entry.tool_uses += 1
        entry.current_action = self._describe_action(tool_name, args)
        self._refresh_line(run_id)

    def record_tool_end(self, run_id: str) -> None:
        """记录子 agent 的工具调用结束。"""
        entry = self._entries.get(run_id)
        if entry is None:
            logger.debug("record_tool_end: unknown run_id=%s", run_id)
            return
        entry.current_action = "Thinking…"

    def record_model_start(self, run_id: str) -> None:
        """记录子 agent 开始思考。"""
        entry = self._entries.get(run_id)
        if entry is None:
            logger.debug("record_model_start: unknown run_id=%s", run_id)
            return
        entry.current_action = "Thinking…"

    def record_tokens(self, run_id: str, usage: dict | None) -> None:
        """累加子 agent 的 token 用量。"""
        if not usage:
            return
        entry = self._entries.get(run_id)
        if entry is None:
            logger.debug("record_tokens: unknown run_id=%s", run_id)
            return
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        if inp:
            entry.input_tokens = max(entry.input_tokens, inp)
        if out:
            entry.output_tokens = max(entry.output_tokens, out)

    def record_stream_token(self, run_id: str, text: str) -> None:
        """累积子 agent 的流式输出文本（用于最终 result）。"""
        entry = self._entries.get(run_id)
        if entry is None:
            logger.debug("record_stream_token: unknown run_id=%s", run_id)
            return
        entry.result += text
        if entry.current_action == "Thinking…":
            entry.current_action = "Writing…"

    def finish_agent(self, run_id: str, output: str) -> None:
        """标记子 agent 完成。"""
        entry = self._entries.get(run_id)
        if entry is None:
            logger.warning("finish_agent: unknown run_id=%s", run_id)
            return
        entry.done = True
        entry.current_action = "Done"
        if output:
            entry.result = output
        self._refresh_line(run_id)
        self._check_all_done()

    def finish_agent_error(self, run_id: str, error: str) -> None:
        """标记子 agent 出错。"""
        entry = self._entries.get(run_id)
        if entry is None:
            logger.warning("finish_agent_error: unknown run_id=%s", run_id)
            return
        entry.done = True
        entry.error = True
        entry.current_action = "Error"
        entry.result = error or "Unknown error"
        self._refresh_line(run_id)
        self._check_all_done()

    def get_entry(self, run_id: str) -> AgentEntry | None:
        """获取子 agent 条目。"""
        return self._entries.get(run_id)

    def toggle_agent_detail(self, run_id: str) -> None:
        """切换单个 agent 的详情展开/折叠。"""
        entry = self._entries.get(run_id)
        if entry is None or not entry.done:
            return
        entry.expanded = not entry.expanded
        self._refresh_line(run_id)
        if entry.expanded:
            self.call_after_refresh(lambda: self._mount_agent_detail(run_id))
        else:
            self._remove_agent_detail(run_id)

    @property
    def is_finalized(self) -> bool:
        return self._finalized

    def force_finalize(self) -> None:
        """强制终止 — 中断场景下调用，将未完成的 agent 标记为 error 后 finalize。"""
        if self._finalized:
            return
        for entry in self._entries.values():
            if not entry.done:
                entry.done = True
                entry.error = True
                entry.current_action = "Interrupted"
                entry.result = entry.result or "Interrupted"
        self._finalize()

    # ── 内部渲染 ──

    def _refresh_header(self) -> None:
        """刷新标题行。"""
        try:
            header = self.query_one(f"#ag-header-{id(self)}", _HeaderLine)
        except NoMatches:
            logger.debug("_refresh_header: header widget not ready")
            return

        total = len(self._entries)

        if self._finalized:
            total_tools = sum(e.tool_uses for e in self._entries.values())
            total_tokens = sum(
                e.input_tokens + e.output_tokens for e in self._entries.values()
            )
            errors = sum(1 for e in self._entries.values() if e.error)
            text = Text()
            text.append("● ", style=get_color("success"))
            summary = (
                f"Ran {total} agents "
                f"({total_tools} tool uses · {_format_tokens(total_tokens)} tokens)"
            )
            if errors:
                summary += f" ({errors} failed)"
                text.append(summary, style=get_color("error"))
            else:
                text.append(summary, style=get_color("text_muted"))
            header.update(text)
        else:
            frame = BLINK_FRAMES[self._spinner_frame % len(BLINK_FRAMES)]
            text = Text()
            text.append(frame, style=get_color("accent"))
            text.append(
                f" Running {total} agent{'s' if total != 1 else ''}…",
                style=get_color("text_muted"),
            )
            header.update(text)

    def _refresh_lines(self) -> None:
        """刷新所有 agent 行。"""
        for run_id in self._order:
            self._refresh_line(run_id)

    def _refresh_line(self, run_id: str) -> None:
        """刷新单个 agent 行。"""
        entry = self._entries.get(run_id)
        if entry is None:
            return
        try:
            line = self.query_one(f"#ag-line-{run_id}", _AgentLine)
        except NoMatches:
            logger.debug("_refresh_line: line widget not ready (run_id=%s)", run_id)
            return

        is_last = run_id == self._order[-1] if self._order else False
        prefix = "└─ " if is_last else "├─ "

        text = Text()
        text.append(prefix, style=get_color("text_muted"))

        total_tokens = entry.input_tokens + entry.output_tokens
        stats = (
            f" · {entry.tool_uses} tool uses · {_format_tokens(total_tokens)} tokens"
        )

        if entry.done:
            if entry.error:
                text.append(entry.name, style=f"bold {get_color('error')}")
                text.append(stats, style=get_color("text_muted"))
                text.append("\n│  └ ", style=get_color("text_muted"))
                text.append("Error", style=get_color("error"))
            else:
                text.append(entry.name, style=get_color("text_muted"))
                text.append(stats, style=get_color("text_muted"))
                text.append("\n│  └ ", style=get_color("text_muted"))
                text.append("Done", style=get_color("success"))
        else:
            text.append(entry.name, style="bold")
            text.append(stats, style=get_color("text_muted"))
            action = entry.current_action
            if len(action) > _ACTION_MAX_LEN:
                action = action[:_ACTION_MAX_LEN] + "…"
            text.append("\n│  └ ", style=get_color("text_muted"))
            text.append(action, style=get_color("text_muted"))

        line.update(text)

    def _check_all_done(self) -> None:
        """检查是否所有 agent 都已完成。"""
        if all(e.done for e in self._entries.values()):
            self._finalize()

    def _finalize(self) -> None:
        """所有 agent 完成后最终化。"""
        self._finalized = True
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._refresh_header()
        self._refresh_lines()

    async def _mount_agent_detail(self, run_id: str) -> None:
        """在 agent 行下方挂载该 agent 的 prompt + result 详情。"""
        entry = self._entries.get(run_id)
        if entry is None:
            return

        detail_id = f"ag-detail-{run_id}"
        try:
            self.query_one(f"#{detail_id}", _AgentDetail)
            return
        except NoMatches:
            pass

        detail = _AgentDetail(id=detail_id)

        try:
            container = self.query_one(".agent-lines", Vertical)
            line = self.query_one(f"#ag-line-{run_id}", _AgentLine)
            await container.mount(detail, after=line)
        except NoMatches:
            logger.error(
                "_mount_agent_detail failed: container not found (run_id=%s)",
                run_id,
            )
            return

        # Prompt
        prompt_label = Text("Prompt: ", style=f"italic {get_color('text_muted')}")
        prompt_text = Text.assemble(prompt_label, entry.prompt)
        await detail.mount(
            Static(prompt_text, markup=False, classes="agent-detail-content")
        )

        # Result
        result_label = Text("Result: ", style=f"italic {get_color('text_muted')}")
        result_content = entry.result.strip()
        if entry.error:
            result_text = Text.assemble(
                result_label,
                Text(result_content, style=get_color("error")),
            )
        else:
            result_text = Text.assemble(result_label, result_content)
        await detail.mount(
            Static(result_text, markup=False, classes="agent-detail-content")
        )

    def _remove_agent_detail(self, run_id: str) -> None:
        """移除某个 agent 的详情区域。"""
        detail_id = f"ag-detail-{run_id}"
        try:
            detail = self.query_one(f"#{detail_id}", _AgentDetail)
            detail.remove()
        except NoMatches:
            pass

    @staticmethod
    def _describe_action(tool_name: str, args: dict) -> str:
        """从工具名和参数生成简短的动作描述。"""
        match tool_name:
            case "read" | "read_file":
                path = args.get("file_path", "") or args.get("path", "")
                return f"Reading {path}…" if path else "Reading file…"
            case "write" | "write_file" | "create":
                path = args.get("file_path", "") or args.get("path", "")
                return f"Writing {path}…" if path else "Writing file…"
            case "edit":
                path = args.get("file_path", "") or args.get("path", "")
                return f"Editing {path}…" if path else "Editing file…"
            case "bash":
                cmd = args.get("command", "")
                if cmd:
                    short = cmd[:40]
                    return f"Running `{short}`…"
                return "Running command…"
            case "grep" | "search":
                pattern = args.get("pattern", "") or args.get("query", "")
                return f"Searching `{pattern}`…" if pattern else "Searching…"
            case "glob" | "list_dir":
                return "Listing files…"
            case _:
                return f"{tool_name}…"

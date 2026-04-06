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
from textual.events import Click, Key
from textual.timer import Timer
from textual.widgets import Static

from lumi.tui.renderers.utils import BLINK_FRAMES
from lumi.tui.theme import get_color

logger = logging.getLogger(__name__)

# 当前动作描述的最大长度
_ACTION_MAX_LEN = 60

# 定时刷新间隔（秒）
_SPINNER_INTERVAL = 0.5

# bash 命令预览截断长度
_BASH_CMD_PREVIEW_LEN = 40


@dataclass
class AgentEntry:
    """单个子 agent 的运行统计。"""

    name: str
    prompt: str
    run_id: str
    widget_id: str = ""
    """DOM widget 的固定 id 后缀，始终为初始注册时的 run_id。"""
    tool_uses: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    current_action: str = "Thinking…"
    result: str = ""
    done: bool = False
    error: bool = False
    expanded: bool = False

    def __post_init__(self) -> None:
        if not self.widget_id:
            self.widget_id = self.run_id


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
    """单个子 agent 的摘要行，完成后可点击或按键展开 prompt + result。"""

    can_focus = True

    DEFAULT_CSS = """
    _AgentLine {
        height: auto;
        width: 1fr;
        padding: 0 0 0 2;
        margin: 0;
    }
    _AgentLine:focus {
        text-style: reverse;
    }
    """

    def _toggle(self) -> None:
        for ancestor in self.ancestors_with_self:
            if isinstance(ancestor, AgentGroup):
                wid = self.id or ""
                prefix = "ag-line-"
                if wid.startswith(prefix):
                    run_id = wid[len(prefix) :]
                    ancestor.toggle_agent_detail(run_id)
                break

    def on_click(self, event: Click) -> None:
        """点击时通知 AgentGroup 切换该 agent 的详情展开状态。"""
        event.stop()
        self._toggle()

    def on_key(self, event: Key) -> None:
        if event.key in ("enter", "space"):
            event.stop()
            event.prevent_default()
            self._toggle()


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
        self._order: list[str] = []  # 保持插入顺序（唯一，无 alias）
        self._finalized = False
        self._spinner_frame = 0
        self._spinner_timer: Timer | None = None

    @property
    def _unique_entries(self) -> list[AgentEntry]:
        """返回去重后的 entry 列表（按 _order 顺序）。"""
        return [self._entries[rid] for rid in self._order if rid in self._entries]

    def compose(self) -> ComposeResult:
        yield _HeaderLine("", markup=False, id=f"ag-header-{id(self)}")
        yield Vertical(classes="agent-lines", id=f"ag-lines-{id(self)}")

    def on_mount(self) -> None:
        """挂载后启动 spinner。"""
        self._spinner_timer = self.set_interval(_SPINNER_INTERVAL, self._tick)

    def _tick(self) -> None:
        """定时刷新：更新标题和各 agent 行。"""
        self._spinner_frame += 1
        if not self._finalized:
            self._refresh_header()
            self._refresh_lines()

    # ── 公开 API ──

    async def add_agent(self, run_id: str, name: str, prompt: str) -> None:
        """注册一个新的子 agent。"""
        if run_id in self._entries:
            return
        entry = AgentEntry(name=name, prompt=prompt, run_id=run_id)
        self._entries[run_id] = entry
        self._order.append(run_id)
        try:
            container = self.query_one(".agent-lines", Vertical)
            line = _AgentLine("", markup=False, id=f"ag-line-{run_id}")
            await container.mount(line)
        except NoMatches:
            logger.error(
                "add_agent failed: container not ready (event_type=add_agent, run_id=%s)",
                run_id,
            )
        self._refresh_header()

    def remap_agent(self, old_run_id: str, new_run_id: str) -> bool:
        """将已有条目的 run_id 映射到新值（replay 场景）。

        保留旧 run_id 作为别名，widget_id 不变（DOM 稳定性）。

        Returns:
            True 映射成功，False 旧 run_id 不存在。
        """
        entry = self._entries.get(old_run_id)
        if entry is None:
            return False
        entry.run_id = new_run_id
        # 新 key 指向同一个 entry，旧 key 保留作为别名
        self._entries[new_run_id] = entry
        self._order = [new_run_id if r == old_run_id else r for r in self._order]
        return True

    def _get_entry(self, run_id: str, caller: str = "") -> AgentEntry | None:
        """按 run_id 查找 AgentEntry，未找到时记录日志。"""
        entry = self._entries.get(run_id)
        if entry is None:
            logger.debug("%s: unknown run_id=%s", caller or "AgentGroup", run_id)
        return entry

    def record_tool_start(self, run_id: str, tool_name: str, args: dict) -> None:
        """记录子 agent 的工具调用开始。"""
        if (entry := self._get_entry(run_id, "record_tool_start")) is None:
            return
        entry.tool_uses += 1
        entry.current_action = self._describe_action(tool_name, args)
        self._refresh_line(run_id)

    def record_tool_end(self, run_id: str) -> None:
        """记录子 agent 的工具调用结束。"""
        if (entry := self._get_entry(run_id, "record_tool_end")) is None:
            return
        entry.current_action = "Thinking…"

    def record_model_start(self, run_id: str) -> None:
        """记录子 agent 开始思考。"""
        if (entry := self._get_entry(run_id, "record_model_start")) is None:
            return
        entry.current_action = "Thinking…"

    def record_tokens(self, run_id: str, usage: dict | None) -> None:
        """累加子 agent 的 token 用量。"""
        if not usage:
            return
        if (entry := self._get_entry(run_id, "record_tokens")) is None:
            return
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        if inp:
            entry.input_tokens = max(entry.input_tokens, inp)
        if out:
            entry.output_tokens = max(entry.output_tokens, out)

    def record_stream_token(self, run_id: str, text: str) -> None:
        """累积子 agent 的流式输出文本（用于最终 result）。"""
        if (entry := self._get_entry(run_id, "record_stream_token")) is None:
            return
        entry.result += text
        if entry.current_action == "Thinking…":
            entry.current_action = "Writing…"

    def finish_agent(self, run_id: str, output: str) -> None:
        """标记子 agent 完成。"""
        if (entry := self._get_entry(run_id, "finish_agent")) is None:
            return
        entry.done = True
        entry.current_action = "Done"
        if output:
            entry.result = output
        self._refresh_line(run_id)
        self._check_all_done()

    def finish_agent_error(self, run_id: str, error: str) -> None:
        """标记子 agent 出错。"""
        if (entry := self._get_entry(run_id, "finish_agent_error")) is None:
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

    def _find_entry_by_widget_id(self, widget_id: str) -> AgentEntry | None:
        """通过 widget_id 反查 AgentEntry。"""
        for entry in self._entries.values():
            if entry.widget_id == widget_id:
                return entry
        return None

    def toggle_agent_detail(self, run_id: str) -> None:
        """切换单个 agent 的详情展开/折叠。

        run_id 可以是当前 run_id 或 widget_id，均可正确查找。
        """
        entry = self._entries.get(run_id)
        if entry is None:
            # 可能传入的是 widget_id（来自 on_click），尝试反查
            entry = self._find_entry_by_widget_id(run_id)
        if entry is None or not entry.done:
            return
        entry.expanded = not entry.expanded
        wid = entry.widget_id
        self._refresh_line(entry.run_id)
        if entry.expanded:
            self.call_after_refresh(lambda: self._mount_agent_detail(wid))
        else:
            self._remove_agent_detail(wid)

    @property
    def is_finalized(self) -> bool:
        return self._finalized

    def force_finalize(self) -> None:
        """强制终止 — 中断场景下调用，将未完成的 agent 标记为 error 后 finalize。"""
        if self._finalized:
            return
        for entry in self._unique_entries:
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

        text = (
            self._build_finalized_header()
            if self._finalized
            else self._build_running_header()
        )
        header.update(text)

    def _build_finalized_header(self) -> Text:
        """Build the header text for finalized (all agents done) state."""
        total = len(self._order)
        entries = self._unique_entries
        total_tools = sum(e.tool_uses for e in entries)
        total_tokens = sum(e.input_tokens + e.output_tokens for e in entries)
        errors = sum(1 for e in entries if e.error)

        text = Text()
        text.append("● ", style=get_color("success"))
        summary = f"Ran {total} agent{'s' if total != 1 else ''}"
        if total_tools or total_tokens:
            summary += (
                f" ({total_tools} tool uses · {_format_tokens(total_tokens)} tokens)"
            )
        if errors:
            summary += f" ({errors} failed)"
            text.append(summary, style=get_color("error"))
        else:
            text.append(summary, style=get_color("text_muted"))
        return text

    def _build_running_header(self) -> Text:
        """Build the header text for in-progress state."""
        total = len(self._order)
        frame = BLINK_FRAMES[self._spinner_frame % len(BLINK_FRAMES)]
        text = Text()
        text.append(frame, style=get_color("accent"))
        text.append(
            f" Running {total} agent{'s' if total != 1 else ''}…",
            style=get_color("text_muted"),
        )
        return text

    def _refresh_lines(self) -> None:
        """刷新所有 agent 行。"""
        for run_id in self._order:
            self._refresh_line(run_id)

    def _refresh_line(self, run_id: str) -> None:
        """刷新单个 agent 行。"""
        entry = self._entries.get(run_id)
        if entry is None:
            return
        # widget DOM id 始终使用初始注册时的 widget_id，而非可能被 remap 的 run_id
        wid = entry.widget_id
        try:
            line = self.query_one(f"#ag-line-{wid}", _AgentLine)
        except NoMatches:
            logger.debug("_refresh_line: line widget not ready (widget_id=%s)", wid)
            return

        is_last = run_id == self._order[-1] if self._order else False
        text = self._build_agent_line_text(entry, is_last=is_last)
        line.update(text)

    def _build_agent_line_text(self, entry: AgentEntry, *, is_last: bool) -> Text:
        """Build the Rich Text for a single agent summary line."""
        prefix = "└─ " if is_last else "├─ "
        sub_prefix = "\n   └ " if is_last else "\n│  └ "

        text = Text()
        text.append(prefix, style=get_color("text_muted"))

        stats = self._format_entry_stats(entry)

        if entry.done:
            self._append_done_line(text, entry, stats, sub_prefix)
        else:
            self._append_running_line(text, entry, stats, sub_prefix)
        return text

    @staticmethod
    def _format_entry_stats(entry: AgentEntry) -> str:
        """Format the tool-uses / token stats suffix for an agent entry."""
        total_tokens = entry.input_tokens + entry.output_tokens
        if not entry.tool_uses and not total_tokens:
            return ""
        return f" · {entry.tool_uses} tool uses · {_format_tokens(total_tokens)} tokens"

    @staticmethod
    def _append_done_line(
        text: Text, entry: AgentEntry, stats: str, sub_prefix: str
    ) -> None:
        """Append name/stats/status for a completed agent."""
        if entry.error:
            text.append(entry.name, style=f"bold {get_color('error')}")
            text.append(stats, style=get_color("text_muted"))
            text.append(sub_prefix, style=get_color("text_muted"))
            text.append("Error", style=get_color("error"))
        else:
            text.append(entry.name, style=get_color("text_muted"))
            text.append(stats, style=get_color("text_muted"))
            text.append(sub_prefix, style=get_color("text_muted"))
            text.append("Done", style=get_color("success"))

    @staticmethod
    def _append_running_line(
        text: Text, entry: AgentEntry, stats: str, sub_prefix: str
    ) -> None:
        """Append name/stats/action for a still-running agent."""
        text.append(entry.name, style="bold")
        text.append(stats, style=get_color("text_muted"))
        action = entry.current_action
        if len(action) > _ACTION_MAX_LEN:
            action = action[:_ACTION_MAX_LEN] + "…"
        text.append(sub_prefix, style=get_color("text_muted"))
        text.append(action, style=get_color("text_muted"))

    def _check_all_done(self) -> None:
        """检查是否所有 agent 都已完成。"""
        if all(e.done for e in self._unique_entries):
            self._finalize()

    def _finalize(self) -> None:
        """所有 agent 完成后最终化。"""
        self._finalized = True
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._refresh_header()
        self._refresh_lines()

    async def _mount_agent_detail(self, widget_id: str) -> None:
        """在 agent 行下方挂载该 agent 的 prompt + result 详情。

        参数 widget_id 为 AgentEntry.widget_id（DOM 稳定 id）。
        """
        entry = self._find_entry_by_widget_id(widget_id)
        if entry is None:
            return

        detail_id = f"ag-detail-{widget_id}"
        try:
            self.query_one(f"#{detail_id}", _AgentDetail)
            return  # already mounted
        except NoMatches:
            pass

        detail = _AgentDetail(id=detail_id)
        try:
            container = self.query_one(".agent-lines", Vertical)
            line = self.query_one(f"#ag-line-{widget_id}", _AgentLine)
            await container.mount(detail, after=line)
        except NoMatches:
            logger.error(
                "_mount_agent_detail failed: container not found (widget_id=%s)",
                widget_id,
            )
            return

        await self._mount_detail_content(detail, entry)

    @staticmethod
    async def _mount_detail_content(detail: _AgentDetail, entry: AgentEntry) -> None:
        """Mount prompt and result Static widgets inside a detail container."""
        label_style = f"italic {get_color('text_muted')}"

        # Prompt
        prompt_text = Text.assemble(Text("Prompt: ", style=label_style), entry.prompt)
        await detail.mount(
            Static(prompt_text, markup=False, classes="agent-detail-content")
        )

        # Result
        result_content = entry.result.strip()
        if entry.error:
            result_body = Text(result_content, style=get_color("error"))
        else:
            result_body = Text(result_content)
        result_text = Text.assemble(Text("Result: ", style=label_style), result_body)
        await detail.mount(
            Static(result_text, markup=False, classes="agent-detail-content")
        )

    def _remove_agent_detail(self, widget_id: str) -> None:
        """移除某个 agent 的详情区域。

        参数 widget_id 为 AgentEntry.widget_id（DOM 稳定 id）。
        """
        detail_id = f"ag-detail-{widget_id}"
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
                    short = cmd[:_BASH_CMD_PREVIEW_LEN]
                    return f"Running `{short}`…"
                return "Running command…"
            case "grep" | "search":
                pattern = args.get("pattern", "") or args.get("query", "")
                return f"Searching `{pattern}`…" if pattern else "Searching…"
            case "glob" | "list_dir":
                return "Listing files…"
            case _:
                return f"{tool_name}…"

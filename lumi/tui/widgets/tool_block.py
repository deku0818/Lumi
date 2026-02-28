"""工具调用块组件 - 可折叠，显示运行状态"""

import json

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Static


class ToolBlock(Vertical):
    """工具调用块 - 可折叠，显示工具名称、参数和输出"""

    DEFAULT_CSS = """
    ToolBlock {
        margin: 0 1 0 2;
        height: auto;
    }

    ToolBlock Collapsible {
        background: #18182a;
        border: solid #333340;
        padding: 0;
    }

    ToolBlock .tool-args {
        color: #888899;
        padding: 0 1;
    }

    ToolBlock .tool-output {
        color: #e0e0e0;
        padding: 0 1;
        margin: 1 0 0 0;
    }
    """

    def __init__(self, name: str, args: dict) -> None:
        super().__init__(classes="tool-block")
        self._name = name
        self._args = args

    def compose(self) -> ComposeResult:
        title = f"[bold #ffcc00]{self._name}[/]  [#888899]Running...[/]"
        args_text = self._build_args_text()
        with Collapsible(title=title, collapsed=False, id=f"tool-{id(self)}"):
            yield Static(args_text, classes="tool-args", markup=False)
            yield Static(
                "", classes="tool-output", id=f"tool-output-{id(self)}", markup=False
            )

    def _build_args_text(self) -> str:
        """格式化工具参数为纯文本"""
        if not self._args:
            return ""
        parts = []
        for key, value in self._args.items():
            if isinstance(value, str) and len(value) > 200:
                value = value[:200] + "..."
            elif isinstance(value, (dict, list)):
                try:
                    value = json.dumps(value, ensure_ascii=False, indent=2)
                    if len(value) > 300:
                        value = value[:300] + "..."
                except Exception:
                    value = str(value)
            parts.append(f"{key}: {value}")
        return "\n".join(parts)

    def set_done(self, output: str = "") -> None:
        """标记工具执行完成"""
        # 更新标题
        collapsible = self.query_one(Collapsible)
        collapsible.title = f"[bold #ffcc00]{self._name}[/]  [#4caf50]Done[/]"
        collapsible.collapsed = True

        # 更新输出（纯文本，不走 markup 解析）
        if output:
            display = output if len(output) <= 500 else output[:500] + "\n..."
            output_widget = self.query_one(f"#tool-output-{id(self)}", Static)
            output_widget.update(display)

    def set_error(self, error: str = "") -> None:
        """标记工具执行错误"""
        collapsible = self.query_one(Collapsible)
        collapsible.title = f"[bold #ffcc00]{self._name}[/]  [#ef5350]Error[/]"
        if error:
            output_widget = self.query_one(f"#tool-output-{id(self)}", Static)
            output_widget.update(Text(error, style="#ef5350"))

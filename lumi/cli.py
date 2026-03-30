"""Lumi CLI 统一入口

用法:
    lumi                    # 启动 TUI
    lumi -p "query"         # 非交互模式：执行 prompt 后退出
    lumi web-server         # 在浏览器中运行 TUI
"""

from __future__ import annotations

import sys
from typing import Annotated, Optional

import typer

app = typer.Typer(
    name="lumi",
    help="Lumi AI Agent",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    prompt: Annotated[
        Optional[str],
        typer.Option("-p", "--prompt", help="非交互模式：执行 prompt 后退出"),
    ] = None,
    style: Annotated[
        Optional[str],
        typer.Option(
            "-s",
            "--style",
            help="系统提示词风格（如 code），覆盖 config.yaml 中的 style 配置",
        ),
    ] = None,
    privileged_danger: Annotated[
        bool,
        typer.Option(
            "--privileged-danger",
            help="特权模式：跳过所有工具审批（危险）",
            is_flag=True,
        ),
    ] = False,
) -> None:
    """启动 Lumi。无参数时打开 TUI，-p 时非交互执行。"""
    if ctx.invoked_subcommand is not None:
        return

    if style is not None:
        from lumi.utils.read_config import get_config

        get_config().set_style_override(style)

    if prompt is not None:
        _run_headless(prompt, privileged=privileged_danger)
    else:
        _run_tui(privileged=privileged_danger)


@app.command("web-server")
def web_server(
    host: str = typer.Option("localhost", help="监听地址"),
    port: int = typer.Option(8000, help="监听端口"),
    title: str = typer.Option("Lumi", help="浏览器标签页标题"),
    debug: bool = typer.Option(False, help="启用 Textual devtools"),
    privileged_danger: bool = typer.Option(
        False, "--privileged-danger", help="特权模式：跳过所有工具审批（危险）"
    ),
) -> None:
    """在浏览器中运行 TUI。"""
    import os

    from textual_serve.server import Server

    if privileged_danger:
        os.environ["LUMI_PRIVILEGED"] = "1"
    else:
        os.environ.pop("LUMI_PRIVILEGED", None)
    command = f"{sys.executable} -m lumi.tui"
    server = Server(command=command, host=host, port=port, title=title)
    server.serve(debug=debug)


def _run_tui(*, privileged: bool = False) -> None:
    """启动终端 TUI。"""
    from lumi.tui.app import LumiApp
    from lumi.utils.patches import apply_all

    apply_all()

    _original = sys.unraisablehook

    def _quiet(args):  # type: ignore[type-arg]
        if args.exc_type is KeyboardInterrupt:
            return
        _original(args)

    sys.unraisablehook = _quiet

    LumiApp(privileged=privileged).run()


def _run_headless(prompt: str, *, privileged: bool = False) -> None:
    """非交互模式：调用 Agent 输出结果后退出。"""
    import asyncio

    from lumi.tui.agent_bridge import AgentBridge, EventKind

    tool_mode = "privileged" if privileged else "auto"

    async def _execute() -> None:
        # 注入 config.yaml 中的环境变量（API key 等）
        from lumi.utils.read_config import get_config

        get_config().apply_env()

        bridge = AgentBridge()
        try:
            await bridge.initialize()
            async for evt in bridge.stream_response(prompt, tool_mode=tool_mode):
                if evt.kind == EventKind.STREAM_TOKEN and evt.text:
                    sys.stdout.write(evt.text)
                    sys.stdout.flush()
                elif evt.kind == EventKind.ERROR:
                    sys.stderr.write(f"\n错误: {evt.error}\n")
                    sys.exit(1)
        finally:
            await bridge.close()
        # 结尾换行
        sys.stdout.write("\n")

    asyncio.run(_execute())


def main() -> None:
    """CLI 主入口。"""
    app()


if __name__ == "__main__":
    main()

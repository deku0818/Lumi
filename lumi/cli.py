"""Lumi CLI 统一入口

用法:
    lumi -p "query"         # 非交互模式：执行 prompt 后退出
    lumi serve              # 启动 WebSocket 服务（供 desktop / web 前端连接）
"""

from __future__ import annotations

import sys
from typing import Annotated

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
        str | None,
        typer.Option("-p", "--prompt", help="非交互模式：执行 prompt 后退出"),
    ] = None,
    style: Annotated[
        str | None,
        typer.Option(
            "-s",
            "--style",
            help="系统提示词风格（如 code），覆盖 config.json 中的 style 配置",
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
    accept_edits: Annotated[
        bool,
        typer.Option(
            "--accept-edits",
            help="自动放行文件编辑(write/edit)，bash 仍需审批",
            is_flag=True,
        ),
    ] = False,
) -> None:
    """运行 Lumi：-p 非交互执行 prompt；无参数显示帮助。前端经 `lumi serve` 连接。"""
    if ctx.invoked_subcommand is not None:
        return

    if style is not None:
        from lumi.utils.read_config import get_config

        get_config().set_style_override(style)

    if prompt is not None:
        _run_headless(prompt, privileged=privileged_danger, accept_edits=accept_edits)
    else:
        typer.echo(ctx.get_help())


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="监听地址"),
    port: int = typer.Option(8765, help="监听端口"),
    token: str = typer.Option(
        "", help="访问令牌；设置后客户端需在 ?token= 携带（公网部署务必设置）"
    ),
    exit_with_parent: bool = typer.Option(
        False,
        "--exit-with-parent",
        help="stdin 关闭（父进程退出）时自动退出；供 Electron sidecar 使用，防孤儿进程",
    ),
) -> None:
    """启动 desktop WebSocket 服务（供 Electron / web 前端连接）。"""
    import uvicorn

    from lumi.gateway.channels import ws

    if exit_with_parent:
        _watch_parent_exit()
    ws.app.state.token = token
    uvicorn.run(ws.app, host=host, port=port)


def _watch_parent_exit() -> None:
    """守望 stdin：读到 EOF（父进程死亡、管道被 OS 关闭）即整体退出。

    孤儿 sidecar 会与新实例抢同一 checkpoint 数据库，把会话读写悬挂成
    「会话打不开」。stdin 管道是跨平台最可靠的父进程死亡信号（Electron 侧以
    stdio pipe 启动，崩溃/强杀同样触发管道关闭）。os._exit 而非优雅关停：
    父进程已死无人在乎，checkpoint 写入是 SQLite 事务、中断也原子。
    """
    import os
    import threading

    def _watch() -> None:
        try:
            sys.stdin.buffer.read()
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=_watch, daemon=True, name="parent-watch").start()


def _run_headless(
    prompt: str, *, privileged: bool = False, accept_edits: bool = False
) -> None:
    """非交互模式：调用 Agent 输出结果后退出。"""
    import asyncio

    from lumi.gateway.bridge import AgentBridge, EventKind

    if privileged:
        tool_mode = "privileged"
    elif accept_edits:
        tool_mode = "accept_edits"
    else:
        tool_mode = "default"

    async def _execute() -> None:
        # 注入 config.json 中的环境变量（API key 等）
        from lumi.utils.read_config import get_config

        get_config().apply_env()

        bridge = AgentBridge()
        try:
            # 单轮即退、无下一轮自愈：冷池等 MCP 工具就位后再建 agent
            await bridge.initialize(wait_mcp=True)
            async for evt in bridge.stream_response(prompt, tool_mode=tool_mode):
                if evt.kind == EventKind.MESSAGE_DELTA and evt.text:
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

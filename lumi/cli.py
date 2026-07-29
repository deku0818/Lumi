"""Lumi CLI 统一入口

用法:
    lumi -p "query"         # 非交互模式：执行 prompt 后退出
    lumi serve              # 启动 WebSocket 服务（供 desktop / web 前端连接）
    lumi env status         # 核心工具链（uv / node / rg）状态
    lumi env install [tool] # 装齐缺失项 / 装指定工具到工具箱
    lumi feishu config      # 飞书渠道配置读写（key=value；app_secret=- 走 stdin）
    lumi feishu diagnose    # 飞书接入体检（本地环境 + 凭证/权限/事件/发布）
    lumi feishu sync-skills # 飞书技能包 → 渠道绑定项目的 .lumi/skills/
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict
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
    # serve 是多项目网关：进程级配置层（全局层）恒钉在用户级 ~/.lumi，不随启动目录
    # 漂移——否则 dev sidecar 从 Lumi 仓库拉起时，仓库自己的 .lumi 会被发现链当成
    # 全局层，泄漏进所有项目的会话与项目主页。项目专属配置走会话级 project 层
    # （config_layers），显式 LUMI_CONFIG_DIR 仍最高优先（容器/测试用）。
    # 工具箱位置无需在此操心：bin_dir 自己就是机器级的（LumiConfig.toolbox_dir）。
    if not os.environ.get("LUMI_CONFIG_DIR"):
        from pathlib import Path

        from lumi.utils.read_config import get_config

        get_config(str(Path.home() / ".lumi"))

    # 工具箱 bin 追加到 PATH 末尾：agent 子进程可见 uv/rg/node/lark-cli，系统同名优先
    from lumi.gateway.toolbox import inject_path

    inject_path()
    _export_lumi_bin()

    import uvicorn

    from lumi.gateway.channels import ws

    if exit_with_parent:
        _watch_parent_exit()
    ws.app.state.token = token
    uvicorn.run(ws.app, host=host, port=port)


# ------------------------------------------------------------------
# 环境工具箱：与 desktop「设置 → 环境」同一套 toolbox 实现的命令行入口。
# agent 在会话里自助装环境（经 LUMI_BIN 回调）与无 GUI 的 serve 机器都走这里。
# ------------------------------------------------------------------

env_app = typer.Typer(help="环境工具箱：核心工具链（uv / node / rg）的探测与安装")
app.add_typer(env_app, name="env")

_SOURCE_TEXT = {"system": "系统", "toolbox": "工具箱", "missing": "缺失"}


def _echo_tool(tool: dict) -> None:
    version = f" v{tool['version']}" if tool["version"] else ""
    line = f"{tool['name']}: {_SOURCE_TEXT[tool['source']]}{version} {tool['path']}"
    typer.echo(line.rstrip())


@env_app.command("status")
def env_status() -> None:
    """列出核心工具链状态（系统已有的永远优先，工具箱不产生影子副本）。"""
    from lumi.gateway.toolbox import status_all

    state = status_all()
    for tool in state["tools"]:
        _echo_tool(tool)
    typer.echo(f"工具箱目录: {state['bin_dir']}")


@env_app.command("install")
def env_install(
    tool: Annotated[
        str, typer.Argument(help="uv / node / rg；留空则装齐全部缺失项")
    ] = "",
) -> None:
    """下载安装核心工具到工具箱目录（免 sudo、不碰系统全局；已装的跳过）。"""
    from lumi.gateway.toolbox import CORE_TOOLS, install_missing

    if tool and tool not in CORE_TOOLS:
        raise typer.BadParameter(f"未知工具: {tool}（可选：{' / '.join(CORE_TOOLS)}）")

    last_phase = ""

    def progress(phase: str, pct: float | None) -> None:
        # 只在阶段切换时打一行：百分比回调按 64KB 块触发，逐条打出来会淹没调用方
        nonlocal last_phase
        if phase != last_phase:
            last_phase = phase
            typer.echo(f"… {phase}")

    try:
        results = install_missing(progress, (tool,) if tool else CORE_TOOLS)
    except Exception as e:
        # 下载失败（断网 / 代理 / GitHub 不可达）是常态，栈回溯对调用方没有信息量
        typer.echo(f"安装失败: {e}", err=True)
        raise typer.Exit(1) from e
    for status in results:
        _echo_tool(asdict(status))


feishu_app = typer.Typer(
    help="飞书渠道：配置读写与接入体检（与 desktop 渠道页同一份数据）"
)
app.add_typer(feishu_app, name="feishu")


def _parse_channel_field(name: str, raw: str) -> object:
    """key=value 预处理：只做 pydantic 做不了的两件事——未知字段当场拦（模型默认
    忽略多余 key，拼错会静默丢配置）、list[str] 逗号拆分。标量强转与校验交给
    save_feishu 的 model_validate（非法值走「保存失败」出口）。
    """
    from lumi.gateway.channels.config import FeishuChannelConfig

    field = FeishuChannelConfig.model_fields.get(name)
    if field is None:
        known = ", ".join(FeishuChannelConfig.model_fields)
        raise typer.BadParameter(f"未知字段: {name}（可用：{known}）")
    if field.annotation == list[str]:
        return [item for item in raw.split(",") if item]
    return raw


@feishu_app.command("config")
def feishu_config(
    pairs: Annotated[
        list[str] | None,
        typer.Argument(
            help="key=value 逐个设置（如 app_id=cli_xxx enabled=true "
            "allow_from=ou_1,ou_2）；不传则显示当前配置。app_secret=- 从 stdin 读入"
        ),
    ] = None,
) -> None:
    """读写飞书渠道配置（lumi.json 的 channels 分区，运行中的后端会自动应用）。"""
    from lumi.gateway.channels import store

    data = store.load_feishu().model_dump()
    if not pairs:
        secret = data["app_secret"]
        # ${ENV} 引用不是密文，打码反而藏掉唯一有用的信息；短密文露前缀等于全露
        if secret and not secret.startswith("${"):
            data["app_secret"] = secret[:6] + "…" if len(secret) > 10 else "（已设置）"
        for key, value in data.items():
            # 与写入语法往返一致：bool 显示 true/false、列表逗号连接，照抄即可改回去
            if isinstance(value, bool):
                value = "true" if value else "false"
            elif isinstance(value, list):
                value = ",".join(value)
            typer.echo(f"{key}: {value}")
        typer.echo(f"配置文件: {store.config_path()}")
        return

    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise typer.BadParameter(f"应为 key=value 形式: {pair}")
        if key == "app_secret" and value == "-":
            value = sys.stdin.readline().strip()
        data[key] = _parse_channel_field(key, value)
    try:
        store.save_feishu(data)
    except Exception as e:
        # ValueError（启用未绑项目）带的是人话；ValidationError 取首条即可定位
        typer.echo(f"保存失败: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo("已保存；运行中的 Lumi 后端会在几秒内自动应用")


@feishu_app.command("sync-skills")
def feishu_sync_skills() -> None:
    """把 lark-cli 内嵌的飞书技能包导出到渠道绑定项目的 .lumi/skills/（按版本增量）。"""
    from lumi.gateway.channels import store
    from lumi.gateway.toolbox import sync_lark_skills

    workspace = store.load_feishu().workspace
    if not workspace:
        typer.echo("尚未绑定项目：先 lumi feishu config workspace=/项目路径", err=True)
        raise typer.Exit(1)
    try:
        updated = sync_lark_skills(None, workspace)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(f"已同步 {updated} 个技能到 {workspace}/.lumi/skills")


@feishu_app.command("diagnose")
def feishu_diagnose() -> None:
    """接入体检：本地环境 + 凭证 / 权限 / 事件 / 发布，任一 error 则退出码非零。

    妙记已启用时追加妙记四项（lark-cli 配置 / 用户授权 / 妙记权限 / 事件订阅）。
    """
    from concurrent.futures import ThreadPoolExecutor

    from lumi.gateway.channels import store
    from lumi.gateway.channels.feishu import minutes
    from lumi.gateway.channels.feishu.setup import diagnose, local_env_checks

    cfg = store.load_feishu()
    # 三组体检互相独立（各自 spawn lark-cli 子进程 / 走开放平台网络），串行要
    # 2-6s；desktop RPC 同源路径已是并行（channel_rpc），CLI 不该是退化版
    with ThreadPoolExecutor(max_workers=3) as pool:
        local = pool.submit(local_env_checks, cfg.workspace)
        remote = pool.submit(diagnose, cfg.app_id, cfg.app_secret)
        extra = (
            pool.submit(minutes.diagnose, cfg.app_id) if cfg.minutes_enabled else None
        )
        checks = local.result() + remote.result() + (extra.result() if extra else [])
    marks = {"ok": "✓", "warn": "!", "error": "✗"}
    for check in checks:
        detail = " ".join(filter(None, [check["detail"], check["emphasis"]]))
        mark = marks[check["tone"]]
        typer.echo(f"[{mark}] {check['name']}" + (f"  {detail}" if detail else ""))
        for label, key in (
            ("链接", "fix_url"),
            ("提示", "fix_note"),
            ("命令", "fix_cmd"),
        ):
            if check[key]:
                typer.echo(f"    {label}: {check[key]}")
    if any(c["tone"] == "error" for c in checks):
        raise typer.Exit(1)


def _export_lumi_bin() -> None:
    """把本 CLI 的可执行入口暴露为 ``LUMI_BIN``，供 agent 子进程回调（与 inject_path 同处调用）。

    打包版后端躺在 app 的 resources 目录里、不在 PATH 上，agent 的 shell 无从定位它；
    dev 下则是 venv 里的 lumi 脚本。

    子进程的**配置目录**不在这里传：显式 ``LUMI_CONFIG_DIR``（容器/测试）本就随环境
    继承下去，没设时父子各自 ``_pin_user_config_dir`` 钉到同一个 ``~/.lumi``。硬导出
    反而会波及所有子进程——嵌套的 ``lumi -p`` 会被钉在 ``~/.lumi``，丢掉它本该发现的
    项目 ``.lumi/``（prompts / skills / agents）。
    """
    os.environ["LUMI_BIN"] = os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    )


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
        # 与 serve 同源：工具箱 bin 追加到 PATH 末尾，agent 子进程可见 uv/rg/lark-cli
        from lumi.gateway.toolbox import inject_path

        inject_path()
        _export_lumi_bin()

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


def _ensure_ca_bundle() -> None:
    """OpenSSL 默认 CA 路径失效时回退到 certifi。

    PyInstaller 冻结产物里这个路径是**构建机**上的位置（CI runner），装到用户机上必然
    不存在，`ssl.create_default_context()` 于是一张 CA 都加载不到，任何证书链都被判成
    不可信。表现极具迷惑性：requests/httpx 显式用 certifi 故 HTTP 调用全部正常，只有
    走 ssl 默认上下文的连接失败——飞书 WS（lark SDK 不传 ssl 参数）就一直卡在「连接中」，
    而同一份代码 dev 模式跑完全正常（系统 Python 的路径在本机真实存在）。

    只在 cafile 与 capath 双双失效时兜底，dev 与容器环境取值不变；显式设过 SSL_CERT_FILE
    则尊重。capath 也要看——有的系统只靠证书目录建立信任（且可能已被灌入企业自签 CA），
    仅凭 cafile 缺失就改判 certifi 会把系统信任库整个换掉。
    """
    import ssl

    if os.environ.get("SSL_CERT_FILE"):
        return
    paths = ssl.get_default_verify_paths()
    if os.path.exists(paths.openssl_cafile or "") or os.path.isdir(
        paths.openssl_capath or ""
    ):
        return
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()


def main() -> None:
    """CLI 主入口。"""
    _ensure_ca_bundle()
    app()


if __name__ == "__main__":
    main()

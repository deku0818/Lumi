"""Lumi CLI 统一入口

用法:
    lumi -p "query"         # 非交互模式：执行 prompt 后退出
    lumi serve              # 启动 WebSocket 服务（供 desktop / web 前端连接）
    lumi env status         # 核心工具链（uv / node / rg）状态
    lumi env install [tool] # 装齐缺失项 / 装指定工具到工具箱
    lumi feishu config      # 飞书渠道配置读写（key=value；app_secret=- 走 stdin）
    lumi feishu diagnose    # 飞书接入体检（本地环境 + 凭证/权限/事件/发布）
    lumi feishu sync-skills # 飞书技能包 → 渠道绑定项目的 .lumi/skills/
    lumi mcp add/list/...   # MCP 服务器配置（与 desktop MCP 面板同一份数据）
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
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

# 体检结果的行首记号。崩不崩由 _force_utf8_stdio 兜（写侧恒 UTF-8）；这里选
# √ / × 只为渲染——中文 Windows 的旧版控制台按 GBK 配字体，✓ U+2713 不在 GBK
# 字符集里，会掉成方框
_CHECK_MARKS = {"ok": "√", "warn": "!", "error": "×"}


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
    for check in checks:
        detail = " ".join(filter(None, [check["detail"], check["emphasis"]]))
        mark = _CHECK_MARKS[check["tone"]]
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


# ------------------------------------------------------------------
# MCP 服务器配置：与 desktop「MCP 面板」同一份数据（mcp_server.json 两层）。
# agent 在会话里自助接 MCP（lumi-config 技能经 LUMI_BIN 回调）与无 GUI 机器都走这里。
# 运行中的会话轮首自查配置变化（refresh_pool_config），写完即可用、不必重启。
# ------------------------------------------------------------------

mcp_app = typer.Typer(
    help="MCP 服务器配置（与 desktop MCP 面板同一份数据；运行中的会话下一条消息生效）"
)
app.add_typer(mcp_app, name="mcp")

_SCOPES = ("global", "project")
# HTTP 类 transport 的 CLI 别名 → adapter 名（stdio 不在此表：按有无 URL 天然分流）
_HTTP_TRANSPORTS = {"http": "streamable_http", "sse": "sse"}


def _mcp_path(scope: str, project: str) -> Path:
    """按 scope 解析配置文件路径；project 缺省取当前目录（会话的 shell 就在项目根）。"""
    from lumi.gateway.mcp_rpc import resolve_project_dir, server_config_path

    if scope not in _SCOPES:
        raise typer.BadParameter(f"scope 只能是 {' / '.join(_SCOPES)}")
    return server_config_path(scope, resolve_project_dir(scope, project or "."))


def _mcp_read(path: Path) -> dict:
    """宽松读（list/get/test 与 remove 定位用）：损坏当空处理，不阻断查看。"""
    from lumi.gateway.mcp_rpc import read_servers_lenient

    return read_servers_lenient(path)


def _mcp_write(scope: str, project: str, name: str, config: dict | None) -> Path:
    """单个 server 的写入（``config=None`` 即删除）：严格读防抹除 + 原子写，
    与 desktop RPC 同语义（read_servers/write_servers 同源）。"""
    from lumi.gateway.mcp_rpc import read_servers, write_servers

    path = _mcp_path(scope, project)
    try:
        servers = read_servers(path)
    except ValueError as e:
        typer.echo(f"写入失败: {e}", err=True)
        raise typer.Exit(1) from e
    if config is None:
        servers.pop(name, None)
    else:
        servers[name] = config
    write_servers(path, servers)
    return path


def _mcp_save(scope: str, project: str, name: str, config: dict) -> None:
    path = _mcp_write(scope, project, name, config)
    typer.echo(f"已保存 {name} → {path}")
    typer.echo("运行中的会话自下一条消息起生效；可先 lumi mcp test 验证连接")


def _pairs_to_dict(items: list[str], sep: str, what: str) -> dict[str, str]:
    """['K=V', ...] → dict，缺分隔符当场拦，键值两侧空白去除。"""
    out: dict[str, str] = {}
    for item in items:
        key, s, value = item.partition(sep)
        if not s or not key.strip():
            raise typer.BadParameter(f"{what} 应为 KEY{sep}VALUE 形式: {item}")
        out[key.strip()] = value.strip()
    return out


_SCOPE_OPT = typer.Option(
    "project", "--scope", "-s", help="global / project（默认项目层）"
)
_PROJECT_OPT = typer.Option("", "--project", help="项目根目录（默认当前目录）")


@mcp_app.command("add", context_settings={"allow_interspersed_args": False})
def mcp_add(
    name: Annotated[str, typer.Argument(help="服务器名")],
    command_or_url: Annotated[
        str, typer.Argument(help="stdio 启动命令，或 http(s):// 地址")
    ],
    args: Annotated[
        list[str] | None,
        typer.Argument(help="stdio 命令参数（选项须放在 name 之前，或用 -- 分隔）"),
    ] = None,
    scope: str = _SCOPE_OPT,
    project: str = _PROJECT_OPT,
    env: Annotated[
        list[str], typer.Option("--env", "-e", help="stdio 环境变量 KEY=VALUE，可重复")
    ] = [],
    header: Annotated[
        list[str],
        typer.Option("--header", "-H", help="HTTP 头 'Key: Value'，可重复"),
    ] = [],
    transport: Annotated[
        str, typer.Option("--transport", "-t", help="http / sse（默认 URL 走 http）")
    ] = "",
) -> None:
    """添加 MCP 服务器：URL 走 HTTP/SSE，其余按 stdio 命令处理。"""
    # 容忍 claude mcp 风格的 `--` 分隔符：allow_interspersed_args=False 下它不再被
    # click 消费而是漏成字面量，出现在命令位或参数首位都剥掉
    cmd_args = list(args or [])
    if command_or_url == "--" and cmd_args:
        command_or_url, cmd_args = cmd_args[0], cmd_args[1:]
    if cmd_args and cmd_args[0] == "--":
        cmd_args = cmd_args[1:]

    if command_or_url.startswith(("http://", "https://")):
        if transport and transport not in _HTTP_TRANSPORTS:
            raise typer.BadParameter(
                f"URL 服务器的 transport 只能是 {' / '.join(_HTTP_TRANSPORTS)}"
            )
        config: dict = {
            "url": command_or_url,
            "transport": _HTTP_TRANSPORTS.get(transport, "streamable_http"),
        }
        if header:
            config["headers"] = _pairs_to_dict(header, ":", "--header")
    else:
        if transport and transport != "stdio":
            raise typer.BadParameter("非 URL 即 stdio 命令，--transport 无需指定")
        config = {
            "command": command_or_url,
            "args": cmd_args,
            "transport": "stdio",
        }
        if env:
            config["env"] = _pairs_to_dict(env, "=", "--env")
    _mcp_save(scope, project, name, config)


@mcp_app.command("add-json")
def mcp_add_json(
    name: Annotated[str, typer.Argument(help="服务器名")],
    json_config: Annotated[
        str,
        typer.Argument(
            help='服务器配置 JSON，如 \'{"command":"npx","args":["-y","x"]}\''
        ),
    ],
    scope: str = _SCOPE_OPT,
    project: str = _PROJECT_OPT,
) -> None:
    """以 JSON 原样添加（适合照抄各 server README 里的 mcpServers 片段）。"""
    try:
        config = json.loads(json_config)
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"JSON 解析失败: {e}") from e
    if not isinstance(config, dict):
        raise typer.BadParameter("配置须是 JSON 对象")
    _mcp_save(scope, project, name, config)


def _server_summary(cfg: dict) -> str:
    """单行摘要：url 或 command+args，禁用条目标注。"""
    if not isinstance(cfg, dict):
        return str(cfg)
    if cfg.get("url"):
        text = str(cfg["url"])
        if cfg.get("transport") == "sse":
            text += " (sse)"
    else:
        text = " ".join([str(cfg.get("command", "")), *map(str, cfg.get("args", []))])
    if cfg.get("disabled") is True:
        text += "（已禁用）"
    return text


@mcp_app.command("list")
def mcp_list(project: str = _PROJECT_OPT) -> None:
    """分层列出 MCP 服务器（全局 + 项目；项目同名覆盖全局）。"""
    for scope in _SCOPES:
        path = _mcp_path(scope, project)
        servers = _mcp_read(path)
        typer.echo(f"[{scope}] {path}")
        if not servers:
            typer.echo("  (无)")
        for name, cfg in servers.items():
            typer.echo(f"  {name}: {_server_summary(cfg)}")


def _mcp_lookup(name: str, project: str) -> tuple[str, dict] | None:
    """按合并语义找 server：项目层优先，返回 (所在层, 原始配置)。"""
    for scope in reversed(_SCOPES):  # project 先查（同名覆盖全局）
        servers = _mcp_read(_mcp_path(scope, project))
        if name in servers:
            return scope, servers[name]
    return None


@mcp_app.command("get")
def mcp_get(
    name: Annotated[str, typer.Argument(help="服务器名")],
    project: str = _PROJECT_OPT,
) -> None:
    """查看单个服务器的生效配置（项目层覆盖全局层）。"""
    found = _mcp_lookup(name, project)
    if found is None:
        typer.echo(f"未找到: {name}", err=True)
        raise typer.Exit(1)
    scope, cfg = found
    typer.echo(f"[{scope}] {_mcp_path(scope, project)}")
    typer.echo(json.dumps(cfg, ensure_ascii=False, indent=2))


@mcp_app.command("remove")
def mcp_remove(
    name: Annotated[str, typer.Argument(help="服务器名")],
    scope: Annotated[
        str, typer.Option("--scope", "-s", help="global / project；两层同名时必须指定")
    ] = "",
    project: str = _PROJECT_OPT,
) -> None:
    """删除 MCP 服务器（未指定 --scope 时自动定位所在层）。"""
    layers = (
        [scope]
        if scope
        else [s for s in _SCOPES if name in _mcp_read(_mcp_path(s, project))]
    )
    if len(layers) > 1:
        typer.echo(f"{name} 在 global 与 project 两层都有，请指定 --scope", err=True)
        raise typer.Exit(1)
    if not layers or name not in _mcp_read(_mcp_path(layers[0], project)):
        typer.echo(f"未找到: {name}", err=True)
        raise typer.Exit(1)
    _mcp_write(layers[0], project, name, None)
    typer.echo(f"已从 {layers[0]} 层删除 {name}")


@mcp_app.command("test")
def mcp_test(
    name: Annotated[str, typer.Argument(help="服务器名")],
    project: str = _PROJECT_OPT,
) -> None:
    """连接测试：临时连一次已保存的服务器，列出其能力后断开（不影响会话池）。"""
    import asyncio

    found = _mcp_lookup(name, project)
    if found is None:
        typer.echo(f"未找到: {name}", err=True)
        raise typer.Exit(1)

    from lumi.agents.tools.providers.mcp import test_mcp_server

    result = asyncio.run(test_mcp_server(found[1]))
    if not result["ok"]:
        typer.echo(
            f"[{_CHECK_MARKS['error']}] {name} 连接失败: {result['error']}", err=True
        )
        raise typer.Exit(1)
    server = result["server"]
    typer.echo(
        f"[{_CHECK_MARKS['ok']}] {name} 连接成功: {server['name']} v{server['version']}"
        f"（{result['latency_ms']}ms）"
    )
    for kind, items in (
        ("工具", result["tools"]),
        ("提示", result["prompts"]),
        ("资源", result["resources"]),
    ):
        if items:
            names = ", ".join(i.get("name") or i.get("uri", "") for i in items)
            typer.echo(f"  {kind} {len(items)} 个: {names}")


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


def _force_utf8_stdio() -> None:
    """标准流恒定 UTF-8。

    Windows 上 stdout 被管道接走时（desktop 拉 ``lumi serve``、agent 的 shell 跑
    ``lumi feishu diagnose``）编码退回 locale——简中机器即 GBK，输出任何不在 GBK 字符集
    的字符会直接 UnicodeEncodeError 崩掉整条命令。而读侧（shell_session / toolbox /
    minutes）本就一律按 UTF-8 解码，写侧跟上才自洽。真实控制台的 stdout 早已是 UTF-8，
    此处等同空操作。errors 兜住冻结产物等拿不到 UTF-8 的边角情形。
    """
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """CLI 主入口。"""
    _force_utf8_stdio()
    _ensure_ca_bundle()
    app()


if __name__ == "__main__":
    main()

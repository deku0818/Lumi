"""lark-cli profile 同步：每个飞书机器人绑定一个 lark-cli profile。

会话按「项目 → 机器人」注入 ``LARKSUITE_CLI_PROFILE``（``store.shell_env_for`` +
shell env provider），该项目里所有 lark-cli 调用即以本机器人身份出去，与用户自己的
全局 active profile 互不干扰。

profile 名**解析后持久化**（``cfg.cli_profile``），不硬编码派生：lark-cli 强制同一
app_id 只能存在于一个 profile——机器上已有指向本 app 的 profile（如用户早前
``config init`` 建的）就直接复用它，既绕开唯一性冲突，又把该 profile 名下已完成的
用户授权（妙记等）无缝带过来；没有才自建 ``lumi-{bot.id}``。需 lark-cli ≥ 1.0.92
（``LARKSUITE_CLI_PROFILE`` 与非交互 ``profile add`` 是新版能力，见 setup 体检的版本门槛）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from lumi.gateway.channels import store
from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.utils.logger import logger

_CLI = "lark-cli"
_TIMEOUT = 20

# LARKSUITE_CLI_PROFILE 自这个版本起可用（env 选 profile + profile add 非交互）
MIN_CLI_VERSION = (1, 0, 92)


def _own_name(bot_id: str) -> str:
    """Lumi 自建 profile 的命名（仅当机器上没有现成 profile 指向本 app 时才会创建）。"""
    return f"lumi-{bot_id}"


def run_cli(
    *args: str, profile: str = "", input_text: str | None = None
) -> tuple[int, str]:
    """跑一次 lark-cli，返回 (exit_code, 输出)；无法启动时 exit_code = -1。

    飞书包内 lark-cli 子进程的唯一 runner（minutes 等共用，别再抄一份）。两处
    Windows 讲究：走 which 解析完整路径（npm 装出的是 lark-cli.cmd，subprocess
    只会给裸名补 .exe，不像 which 那样遍历 PATHEXT）；显式 UTF-8 解码（text=True
    用系统 locale，中文 Windows 的 cp936 撞上 CLI 的中文输出即报错）。
    profile 非空时经 ``LARKSUITE_CLI_PROFILE`` 指定机器人专属身份。
    """
    exe = shutil.which(_CLI)
    if exe is None:
        return -1, f"{_CLI} 不在 PATH"
    env = {**os.environ, "LARKSUITE_CLI_PROFILE": profile} if profile else None
    try:
        proc = subprocess.run(
            [exe, *args],
            input=input_text,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            env=env,
        )
    except Exception as e:
        return -1, str(e)
    return proc.returncode, proc.stdout or proc.stderr


def _list_profiles() -> list[dict] | None:
    """现有 profile 列表；lark-cli 不可用/输出异常返回 None。"""
    code, out = run_cli("profile", "list")
    if code != 0:
        return None
    try:
        profiles = json.loads(out)
        return profiles if isinstance(profiles, list) else None
    except ValueError:
        return None


def profile_status(app_id: str, cli_profile: str) -> tuple[str, str]:
    """机器人 profile 的现状 ``(状态, 详情)``。

    状态四值：``ok`` / ``missing``（未同步或已丢失）/ ``mismatch``（app 换了）/
    ``error``（判不了，详情给原因）。状态恒是这四个 token——把人话混进同一个
    返回值会让消费方靠猜区分枚举与详情。
    """
    app_id = os.path.expandvars(app_id)
    if not app_id:
        return "error", "凭证未配置"
    if not cli_profile:
        return "missing", ""
    profiles = _list_profiles()
    if profiles is None:
        return "error", "lark-cli 不可用或 profile 列表读取失败"
    existing = next((p for p in profiles if p.get("name") == cli_profile), None)
    if existing is None:
        return "missing", ""
    return ("ok", "") if existing.get("appId") == app_id else ("mismatch", "")


def sync_profile(cfg: FeishuChannelConfig) -> tuple[str, str]:
    """解析并物化本机器人的 lark-cli profile，返回 ``(profile 名, 错误)``。

    成功时错误为空串、profile 名由调用方写回 ``cfg.cli_profile``；失败时 profile 名
    为空串。解析顺序：已记录的仍有效 → 复用现成指向本 app 的 profile（用户授权跟着
    带过来）→ 自建 ``lumi-{id}``。用户自己的 profile 恒不删不改——app 换绑那种失效
    只重建我们自建的那个。
    """
    app_id = os.path.expandvars(cfg.app_id)
    secret = os.path.expandvars(cfg.app_secret)
    if not app_id or not secret:
        return "", "凭证未配置"
    profiles = _list_profiles()
    if profiles is None:
        return "", "lark-cli 不可用或 profile 列表读取失败"

    own = _own_name(cfg.id)
    recorded = next(
        (p for p in profiles if cfg.cli_profile and p.get("name") == cfg.cli_profile),
        None,
    )
    if recorded is not None and recorded.get("appId") == app_id:
        return cfg.cli_profile, ""
    if recorded is not None and cfg.cli_profile == own:
        run_cli("profile", "remove", own)  # 自建的但 app 换了：重建

    # 指向本 app 的现成 profile 一律复用——含早前自建但 cli_profile 记录丢失的
    # ``lumi-{id}``（不复用会走 profile add 撞重名，且每次保存都撞，永无自愈）
    existing = next((p for p in profiles if p.get("appId") == app_id), None)
    if existing is not None:
        logger.info(
            f"[lark-profile] 机器人「{cfg.name}」复用现成 profile {existing.get('name')}"
        )
        return str(existing.get("name")), ""

    code, out = run_cli(
        "profile",
        "add",
        "--name",
        own,
        "--app-id",
        app_id,
        "--app-secret-stdin",
        input_text=secret,
    )
    if code != 0:
        return "", out.strip()[:200] or "profile add 失败"
    logger.info(f"[lark-profile] 已创建机器人「{cfg.name}」专属 profile {own}")
    return own, ""


def save_bot_synced(config: dict) -> tuple[FeishuChannelConfig, str]:
    """校验 →（必要时）同步 lark-cli profile → 一次性落盘，返回 ``(配置, 提示)``。

    CLI 与 ``save_channel`` RPC 共用的唯一保存路径：先校验（约束不过不跑同步，
    免得为存不进去的配置建出孤儿 profile）、再解析 profile、最后单次写盘——
    watch_store 只见最终态，不会因「先存后补写 cli_profile」的中间态多弹一次连接。
    app 未变且 profile 已有记录时跳过同步：纯开关/白名单改动不必付一次 lark-cli
    子进程。同步 best-effort——lark-cli 缺失/旧版不挡保存（提示非空），体检兜底。
    """
    validated = store.validate_feishu_bot(config)
    prev = next((b for b in store.load_feishu_bots() if b.id == validated.id), None)
    notice = ""
    if (
        prev is not None
        and prev.cli_profile
        and store.same_app(prev.app_id, validated.app_id)
    ):
        # app 未变且已同步过：profile 以服务端记录为准（前端只透传，不当授权来源）
        validated = validated.model_copy(update={"cli_profile": prev.cli_profile})
    else:
        profile, notice = sync_profile(validated)
        if profile:
            validated = validated.model_copy(update={"cli_profile": profile})
    return store.persist_feishu_bot(validated), notice


def remove_profile(bot_id: str, cli_profile: str) -> None:
    """回收某机器人的 profile（删除机器人时调用，best-effort）。

    只删我们自建的 ``lumi-{id}``——复用的用户 profile 不属于 Lumi，删了会连
    用户自己的登录态一起清掉。入参是裸字段而非配置对象：删除路径拿到的可能是
    校验不过的坏条目（见 ``store.delete_feishu_bot``），坏条目也得能回收。
    """
    if not bot_id or cli_profile != _own_name(bot_id):
        return
    code, out = run_cli("profile", "remove", cli_profile)
    if code != 0 and "not found" not in out:
        logger.warning(f"[lark-profile] 回收 {cli_profile} 失败: {out.strip()[:200]}")

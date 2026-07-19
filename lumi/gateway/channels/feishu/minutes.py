"""飞书妙记：事件订阅与链路诊断。

妙记链路有四个彼此独立的前置条件——lark-cli 可用、用户已授权、应用已开通妙记
权限、服务端事件订阅生效——任一断裂的表现完全相同：**静默收不到事件，无任何
报错**。故单列出逐项诊断供配置界面展示，把"不工作"变成"卡在第几步"。

订阅接口只认 user_access_token 而 Lumi 只持有 app 身份，故一律 shell out 调
lark-cli（agent 取逐字稿本就依赖它，非新增依赖）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass

from lumi.utils.logger import logger

MINUTE_EVENT = "minutes.minute.generated_v1"

# 读妙记内容 + 订阅事件所需 scope；缺任一都会静默失效
REQUIRED_SCOPES = ("minutes:minutes.basic:read", "minutes:minutes.transcript:export")

_CLI = "lark-cli"
_TIMEOUT = 20

# login 只请求勾选/参数指定的 scope（应用开通了也不会自动带上），必须显式列出所需项；
# --scope 与 --recommend 叠加，不会丢掉其他常用权限
_LOGIN_CMD = f'{_CLI} auth login --recommend --scope "{",".join(REQUIRED_SCOPES)}"'

# 四项检查的固定顺序与显示名。前一项不通时其后各项统一标记为「需先完成上一步」，
# 由 _with_blocked_tail 按本表补齐——各失败分支不再手写剩余项，免得步骤表漂移。
_STEPS: tuple[tuple[str, str], ...] = (
    ("cli", "lark-cli"),
    ("auth", "用户授权"),
    ("scope", "妙记权限"),
    ("subscription", "事件订阅"),
)


@dataclass(frozen=True)
class MinuteCheck:
    """一项诊断结果。fix_* 为空表示该项无需修复引导。"""

    key: str  # cli / auth / scope / subscription
    ok: bool
    name: str
    detail: str = ""
    fix_cmd: str = ""  # 可复制的终端命令
    fix_url: str = ""  # 开放平台直达链接
    fix_note: str = ""  # 补充说明


def _run_cli(*args: str) -> tuple[bool, str]:
    """跑一次 lark-cli，返回 (成功, stdout 或错误原因)。"""
    try:
        proc = subprocess.run(
            [_CLI, *args], capture_output=True, text=True, timeout=_TIMEOUT
        )
    except FileNotFoundError:
        return False, f"{_CLI} 不在 PATH"
    except Exception as e:  # 超时 / 其他执行失败
        return False, str(e)
    return True, proc.stdout or proc.stderr


def ensure_subscription() -> str:
    """幂等重建妙记事件订阅；成功返回空串，失败返回原因。

    每次 channel 启动都调：订阅会因 `lark-cli event consume` 优雅退出（它会主动
    unsubscribe）、user token 过期、换机器授权等多种原因失效，而失效是静默的。
    """
    ok, out = _run_cli(
        "api",
        "POST",
        "/open-apis/minutes/v1/minutes/subscription",
        "--data",
        json.dumps({"event_type": MINUTE_EVENT}),
        "--as",
        "user",
    )
    if not ok:
        return out
    try:
        payload = json.loads(out)
    except ValueError:
        return (out or "无输出").strip()[:200]
    if payload.get("ok"):
        return ""
    error = payload.get("error") or {}
    return str(error.get("message") or payload)[:200]


def _auth_status() -> dict | None:
    """lark-cli auth status --json 的解析结果；不可用时 None。"""
    ok, out = _run_cli("auth", "status", "--json")
    if not ok:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def transcript_hint(token: str, tmp_dir: str) -> str:
    """妙记生成后注入 agent 的合成轮提示。

    三条刻意的选择：只陈述情境（用户刚开完会 / 录了语音）不规定产出形式，交给 agent
    判断；直接给出取数命令，省掉 list skill → 读 skill → 试参数的探索开销；事件语义
    即「已生成完成」，此刻逐字稿必然可读，无需重试退避。

    落盘先 cd 到临时区：工具默认写 ./minutes/，会把含敏感内容的会议记录留在工作区；
    不用 --output-dir——它只收「当前目录内的相对路径」，绝对路径直接报 invalid_argument。
    命令拼装留在本模块，与其余 lark-cli 知识同处一地。
    """
    return (
        "<system-reminder>\n"
        "用户刚开完一场会，或录制了一段个人语音，飞书已生成对应妙记，"
        "请询问用户下一步的动作，如：生成纪要，制定后续工作任务。\n"
        f"minute_token: {token}\n"
        "逐字稿此刻已可读取，可以使用下面的命令获取：\n"
        f"  cd {tmp_dir} && {_CLI} minutes +detail --minute-tokens {token} "
        "--transcript --as user\n"
        f"逐字稿落在 {tmp_dir}/minutes/{token}/transcript.txt，带说话人与时间戳。\n"
        "</system-reminder>"
    )


def _with_blocked_tail(checks: list[MinuteCheck], why: str) -> list[dict]:
    """已完成的检查 + 其余各步统一标记 blocked，一次转成 wire 格式。"""
    done = {c.key for c in checks}
    return [asdict(c) for c in checks] + [
        asdict(MinuteCheck(key=key, ok=False, name=name, detail=why))
        for key, name in _STEPS
        if key not in done
    ]


def diagnose(app_id: str) -> list[dict]:
    """逐项体检妙记链路，返回可直接下发给 desktop 的 dict 列表。

    同步实现（子进程 + 网络），调用方需丢线程池。前一项不通即短路返回，后续项由
    _with_blocked_tail 补成「需先完成上一步」——省掉必然失败的探测调用。
    """
    # app_id 支持 ${ENV_VAR} 引用（见 FeishuChannelConfig），不展开会拼出
    # https://open.feishu.cn/app/${FEISHU_APP_ID}/auth 这种点不开的修复链接
    app_id = os.path.expandvars(app_id)
    auth_url = (
        f"https://open.feishu.cn/app/{app_id}/auth"
        # token_type=user：lark-cli 以 --as user 取数，scope 必须加在「用户身份权限」
        # tab 下；tenant 侧开通不会进 user_access_token，且页面默认停在 tenant tab
        f"?q={','.join(REQUIRED_SCOPES)}&op_from=openapi&token_type=user"
    )
    event_url = f"https://open.feishu.cn/app/{app_id}/event"
    checks: list[MinuteCheck] = []

    # ① lark-cli 可用性
    if shutil.which(_CLI) is None:
        checks.append(
            MinuteCheck(
                key="cli",
                ok=False,
                name="lark-cli 未安装",
                detail="妙记取数与事件订阅依赖该命令行工具",
                fix_cmd="npm i -g @larksuite/cli",
            )
        )
        return _with_blocked_tail(checks, "需先安装 lark-cli")
    checks.append(MinuteCheck(key="cli", ok=True, name="lark-cli 已安装"))

    # ② 用户授权（订阅与读逐字稿都必须 user 身份，app 身份读会被拒 2091005）
    user = (_auth_status() or {}).get("identities", {}).get("user") or {}
    if user.get("tokenStatus") != "valid":
        checks.append(
            MinuteCheck(
                key="auth",
                ok=False,
                name="尚未授权或授权已失效",
                detail=user.get("message") or "读取妙记内容需以用户身份登录",
                fix_cmd=_LOGIN_CMD,
                fix_note="终端执行后扫码授权；refresh_token 有效期 7 天，届时需重新登录",
            )
        )
        return _with_blocked_tail(checks, "需先完成授权")
    checks.append(
        MinuteCheck(
            key="auth", ok=True, name="用户已授权", detail=user.get("userName", "")
        )
    )

    # ③ 应用是否开通妙记 scope
    granted = set((user.get("scope") or "").split())
    missing = [s for s in REQUIRED_SCOPES if s not in granted]
    if missing:
        checks.append(
            MinuteCheck(
                key="scope",
                ok=False,
                name="缺少妙记权限",
                detail="应用未开通：" + "、".join(missing),
                fix_url=auth_url,
                fix_cmd=_LOGIN_CMD,
                fix_note="需开通在「用户身份权限」下（应用身份不生效），开通并发布版本后执行上述命令重新授权",
            )
        )
        return _with_blocked_tail(checks, "需先开通权限")
    checks.append(
        MinuteCheck(key="scope", ok=True, name="妙记权限已开通", detail="读取 + 订阅")
    )

    # ④ 服务端订阅（顺带把它重建好——诊断即修复）
    error = ensure_subscription()
    if error:
        logger.warning(f"妙记订阅诊断失败: {error}")
        checks.append(
            MinuteCheck(
                key="subscription",
                ok=False,
                name="事件订阅未生效",
                detail=error,
                fix_url=event_url,
                fix_note=f"确认开放平台「事件与回调」已添加 {MINUTE_EVENT}，添加后需发布版本",
            )
        )
    else:
        checks.append(
            MinuteCheck(
                key="subscription", ok=True, name="事件订阅生效", detail=MINUTE_EVENT
            )
        )
    return [asdict(c) for c in checks]

"""飞书配置诊断的共用结构。

机器人接入与妙记链路各有一串彼此独立的前置条件，任一断裂的表现都是「静默不工作、
零报错」。两者都做成逐项诊断，把「不工作」变成「卡在第几步」，故共用同一套结果结构
与渲染（desktop 侧 CheckRow）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

# 一项检查的三态。「能用」与「完好」必须分开：只有 ok/fail 两态时，缺可选权限只能
# 记成 ok，汇总条便会报「全部生效」，而详情里明明写着某项不可用——自相矛盾。
# 由后端定而非前端从两个布尔拼——ok=False+warn=True 这种非法组合根本不该能构造。
Tone = Literal["ok", "warn", "error"]


@dataclass(frozen=True)
class Check:
    """一项诊断结果。fix_* 为空表示该项无需修复引导。"""

    key: str
    name: str
    tone: Tone = "ok"
    detail: str = ""
    fix_cmd: str = ""  # 可复制的终端命令
    fix_url: str = ""  # 开放平台直达链接
    fix_note: str = ""  # 补充说明
    # 接在 detail 之后加粗显示的内容（如「哪些功能不可用」）
    emphasis: str = ""


def blocked_tail(
    checks: list[Check], steps: tuple[tuple[str, str], ...], why: str
) -> list[dict]:
    """已完成的检查 + 其余各步统一标记 blocked，一次转成 wire 格式。

    前一项不通时后续探测必然失败，与其逐条真跑不如统一标记——各失败分支也就不必
    手写剩余项，免得步骤表漂移。
    """
    done = {c.key for c in checks}
    return [asdict(c) for c in checks] + [
        asdict(Check(key=key, name=name, tone="error", detail=why))
        for key, name in steps
        if key not in done
    ]

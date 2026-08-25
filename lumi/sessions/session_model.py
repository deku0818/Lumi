"""会话模型解析 —— 「这个会话该用哪个模型、哪个思考档位」的单一事实源。

两层，desktop 与 IM 渠道共用（不各自推导）：

    会话覆盖（session_meta 按 thread_id）> 新会话默认（providers.active）

会话覆盖既来自用户显式切换（desktop 选择器 / IM ``/model``），也来自 :func:`pin` ——
会话第一轮开跑时把当时的默认**固化**下来。固化是「模型随会话持久」的实现：此后改
新会话默认不再波及任何聊过的会话，prompt 缓存不会在用户背后失效。没开跑过的空
会话不固化，故仍跟随默认。

覆盖与 pinned/title 同居一条 meta，`delete_meta`（清空 / 删除会话）天然连它一并清。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from lumi.models import provider_store
from lumi.models.manager import allowed_levels
from lumi.sessions.session_meta import load_all, update_meta
from lumi.utils.logger import logger


@dataclass(frozen=True)
class SessionModel:
    """某会话生效的模型 + 连接 + 档位。"""

    model: str
    provider: str
    effort: str | None
    """None = 跟随 profile 按模型解析的档位；非 None 为会话级覆盖，绕过 profile。"""
    pinned: bool
    """是否已固化到该会话（False = 当前沿用新会话默认，尚未开跑或已被清除）。"""


def resolve(thread_id: str) -> SessionModel:
    """解析该会话应然生效的模型（读盘，无缓存）。

    覆盖指向的模型已不在任何连接下（用户删了模型或删了连接）时就地清除自愈——
    不校验就会拿死模型名打空连接，会话永久卡死。
    """
    meta = load_all().get(thread_id, {})
    model = meta.get("model", "")
    if model:
        resolved = provider_store.resolve(model, meta.get("model_provider", ""))
        if resolved.provider:
            return SessionModel(
                resolved.model,
                resolved.provider,
                _valid_effort(meta, resolved.model),
                True,
            )
        update_meta(thread_id, model="", model_provider="", effort="")
        logger.warning(
            f"[SessionModel] thread={thread_id} 会话模型覆盖失效已清除: {model}"
        )
        meta = {}
    # 沿用新会话默认时会话级档位照样生效：只设过 /effort 没设过 /model 是常态
    resolved = provider_store.resolve()
    return SessionModel(
        resolved.model, resolved.provider, _valid_effort(meta, resolved.model), False
    )


def _valid_effort(meta: dict, model: str) -> str | None:
    """会话级档位，不在该模型能力内则视同未设（换过模型后旧档位不再适用）。"""
    level = meta.get("effort", "")
    return level if level and level in allowed_levels(model) else None


def pin(thread_id: str) -> SessionModel:
    """把当前生效的模型固化到该会话；已固化则原样返回（真人轮首调用，幂等）。"""
    current = resolve(thread_id)
    if current.pinned:
        return current
    update_meta(thread_id, model=current.model, model_provider=current.provider)
    return replace(current, pinned=True)


def set_model(thread_id: str, model: str, provider: str) -> None:
    """切换该会话的模型；空值即清除覆盖（落回新会话默认）。

    一并清档位：档位依附模型，换模型后旧档位多半不在新模型的能力内。
    """
    update_meta(thread_id, model=model, model_provider=provider, effort="")


def set_effort(thread_id: str, level: str) -> None:
    """设该会话的思考档位；``auto`` / 空即清除（回到跟随 profile）。"""
    update_meta(thread_id, effort="" if level == "auto" else level)

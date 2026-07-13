"""目标驱动（``/goal``）：session 级 Stop 条件评估。

``/goal <条件>`` 设定一个**可判定的条件**后，agent 被目标驱动持续工作：每次它想
结束（Stop）时，本 hook 用一次独立的 LLM 判定条件是否成立——未成立就注入
``<system-reminder>`` 拉回继续，成立（或永远达不成）则放行结束。移植自 Claude Code
的 ``/goal``。

**判官既不 fork 主 agent、也不是带工具的 agent**，而是一次无状态的
``structured_output`` chain 调用：把对话转录渲染成纯文本作单条 user 内容喂给判官
系统提示。这是个全新 prompt，与主对话滚动缓存零交集——验收不扰主缓存。

**条件存 sidecar 而非 LangGraph state**（见 ``session_meta.get_goal``）：达成时本
hook 清条件（副作用）并返回 ``None``，让 dispatch 继续跑到 ``auto_dream_stop_hook``；
若存 state，清条件必须靠 ``Command(goto=END)``，会 first_intercept 短路掉 dream。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lumi.agents.core.hooks.schema import AdditionalContext, HookContext, HookResult
from lumi.models.chain import structured_output
from lumi.sessions.message_text import extract_messages_as_text
from lumi.sessions.session_meta import get_goal, update_meta
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config
from lumi.utils.sizing import BYTES_PER_TOKEN, text_size

# 转录预算占上下文窗口的比例：留 20% 给判官系统提示 + 三态输出 + 截断说明。
# 这是防止判官调用自己撑爆窗口（超窗=API 400）的安全阀，正常会话根本碰不到。
TRANSCRIPT_BUDGET_RATIO = 0.8


# ① 激活注入 —— /goal <条件> 那一轮的驱动消息（system-reminder，一次）。
# 条件文本原样嵌入不改写；行为契约一次说清（立即开工 / 条件即指令 / 别问用户 /
# 会拦你 / 成功别提 clear）。
ACTIVATION_REMINDER = """<system-reminder>
一个会话级目标已激活，达成条件："{condition}"。简短确认这个目标，然后立即开始（或继续）朝它推进——把这个条件本身当作你的指令，不要停下来问用户该做什么。在条件满足前，系统会拦截你结束对话。条件一旦达成会自动解除——成功后不要提示用户去运行 `/goal clear`，那只用于提前解除目标。
</system-reminder>
"""

# ② 判官系统提示 —— 判官那次独立调用的 system。四条纪律：reason 恒填引证据、
# 证据不足=未完成（default-deny）、声称不可能只是证据不是证明、进度慢≠impossible。
JUDGE_SYSTEM = """你在评估一个"停止条件"钩子。仔细阅读下面的对话转录，判断用户给定的条件是否已满足。

你的回复必须是以下三种形态之一：
- {{"ok": true, "reason": "<引用转录中满足条件的证据文本>"}}
- {{"ok": false, "reason": "<引用缺失了什么、或什么阻碍了条件成立>"}}
- {{"ok": false, "impossible": true, "reason": "<说明为何该条件永远无法满足>"}}

必须始终填写 "reason"，尽可能引用转录中的具体文本。若转录中没有清晰证据表明条件已满足，返回 {{"ok": false, "reason": "转录中证据不足"}}。

仅当条件在本会话中确实无法达成时才用 impossible: true——例如条件自相矛盾、依赖不可用的资源或能力、或助手已明确尝试并穷尽合理方法后声明做不到。此处需你独立判断：助手声称不可能只是证据，不是证明，请自行确认条件确实无法达成，而非顺从助手的自我评估。不要仅因目标尚未达成或进展缓慢就判 impossible。拿不准时，返回不带 impossible 的 {{"ok": false}}。

待判定的条件：
{condition}"""

# ③ 截断说明 —— 转录超预算时前置。判官有自己更小的上下文；装不下截头保尾；
# 截断遇证据缺失宁可误判未完成多拉一轮（与 default-deny 一贯）。
TRUNCATION_NOTE = (
    "[为适配评估器的上下文窗口，较早的对话已被截断——省略了前面 {n} 条消息。"
    "请针对下面的近期转录评估条件；若所需证据可能在被省略的前缀里，"
    '返回 {{"ok": false, "reason": "转录中证据不足"}}。]'
)

# ④ 拦截注入 —— 未达成时拉回的 reminder。极简一行，不重复①的行为指令。
CONTINUATION = "目标条件未满足，已拦截结束：{reason}"


class _GoalVerdict(BaseModel):
    """报告给定的停止条件是否已被满足。"""

    ok: bool = Field(description="条件是否已满足")
    reason: str = Field(description="判定理由，尽量引用对话转录中的具体证据文本")
    impossible: bool = Field(
        default=False, description="条件是否永远无法满足（仅在确实无法达成时才置 true）"
    )


def _render_transcript(messages: list) -> str:
    """渲染转录并按预算截头保尾，超预算时前置截断说明。

    先整体渲染一次；未超预算（常态）直接返回，热路径零逐条开销。超预算才从尾部
    按渲染字节保留消息、丢头——保尾丢头符合"近期证据最相关"，与 default-deny 一致
    （证据可能在被丢的头部→判 false）。字节量按 ``extract_messages_as_text`` 的实际
    渲染文本衡量（含工具调用 ``name({args})``），不用 content 字节——tool_calls 的
    AI 消息 content 常为空，只算 content 会把工具密集尾部严重低估。
    """
    budget = int(
        get_config().config.token.context_length
        * TRANSCRIPT_BUDGET_RATIO
        * BYTES_PER_TOKEN
    )
    full = extract_messages_as_text(messages or [])
    if text_size(full) <= budget:
        return full

    # 超预算（冷路径）：每条独立渲染一次（extract 无跨条状态），滤除空渲染（system /
    # 空合成消息），从尾部按字节保留——N 直接是丢弃的渲染行数，无需再扫 dropped。
    lines = [t for m in (messages or []) if (t := extract_messages_as_text([m]))]
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        used += text_size(line)
        if used > budget and kept:
            break
        kept.append(line)
    kept.reverse()

    omitted = len(lines) - len(kept)
    body = "\n".join(kept)
    return f"{TRUNCATION_NOTE.format(n=omitted)}\n\n{body}" if omitted else body


async def _judge(condition: str, messages: list) -> _GoalVerdict:
    """跑一次判官：转录纯文本作 user 内容，复用会话模型，输出三态。

    转录经 ``{transcript}`` 占位传入（不拼进模板字符串）——转录含工具参数的
    ``{...}``，直接拼会被 ChatPromptTemplate 当模板变量解析而 KeyError。

    不显式传 model_name：``structured_output`` 缺省即 ``create_llm(model_name=None)``
    → 解析会话 active 模型 + 连接（与显式 ``resolve()`` 同源），且内部 force_no_thinking。
    """
    chain = structured_output(
        template="{transcript}",
        structure=_GoalVerdict,
        system_prompt=JUDGE_SYSTEM.format(condition=condition),
    )
    return await chain.ainvoke({"transcript": _render_transcript(messages)})


async def goal_stop_hook(ctx: HookContext) -> HookResult:
    """会话有活跃 goal 时，模型想结束前判定条件是否成立。

    - 无 goal → ``None`` 放行。
    - ok:true → 清 goal（副作用）+ ``None`` 放行结束（dispatch 继续，dream 正常触发）。
    - ok:false → ``AdditionalContext``（short-circuit dream）拉回 CallModel 继续。
    - ok:false + impossible → 清 goal + ``None`` 放行结束（条件永远达不成，别烧循环）。

    判官复用会话模型且 ``structured_output`` 自带重试；重试后仍失败说明会话本身
    也会失败，照常抛出（不做 fail-open）。
    """
    # 子 agent（depth>0）不参与目标驱动：它经 contextvar 继承父 thread_id，若不挡
    # 会拿子 agent 的无关转录去判父 goal（误拉回子 agent / 误清父目标）。主 agent
    # depth=0/缺省。goal 是会话级概念，只对主对话生效。
    if ctx.state.get("depth"):
        return None
    configurable = ctx.config.get("configurable", {}) if ctx.config else {}
    thread_id = configurable.get("thread_id")
    if not thread_id:
        return None
    condition = get_goal(thread_id)
    if not condition:
        return None

    verdict = await _judge(condition, ctx.state.get("messages") or [])
    logger.info(
        "[goal] 判定 ok=%s impossible=%s reason=%s",
        verdict.ok,
        verdict.impossible,
        verdict.reason,
    )

    if verdict.ok or verdict.impossible:
        # 达成或永远达不成：清 goal 放行结束（保留 pin/rename）
        update_meta(thread_id, goal="")
        return None
    return AdditionalContext(CONTINUATION.format(reason=verdict.reason))

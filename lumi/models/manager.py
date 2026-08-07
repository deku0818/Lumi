"""LLM 实例创建与缓存。

协议由模型名自动判定（claude / anthropic / minimax → ChatAnthropic，其余 → ChatOpenAI；
Bedrock 形如 us.anthropic.claude-* 同样走 ChatAnthropic 客户端）。
未显式传连接时，由 provider_store 按 active profile 解析 base_url / api_key。

也是**厂商方言与端点怪癖**的落点（名字 → 厂商的判定、档位 → 协议参数的映射、
「该端点不吃什么」的判定）：catalog 只放 models.dev 的能力数据，端点写法与怪癖一律
在这里，不要下沉到 catalog、也不要就近散进 chain。
"""

import json
import os
from typing import TYPE_CHECKING, Any, Literal

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.models.catalog import ModelEntry

Protocol = Literal["anthropic", "openai"]

_cache: dict[str, Any] = {}


class DialectChatOpenAI(ChatOpenAI):
    """保留方言推理字段 reasoning_content 的 ChatOpenAI。

    DeepSeek / Kimi / MiMo / Qwen 等 OpenAI 兼容端点把思考增量放在
    delta.reasoning_content（非标字段），ChatOpenAI 会静默丢弃——导致
    思考期间前端收不到任何增量，长思考看起来像卡死。这里在流式转换后
    把它补进 additional_kwargs；请求构造不读该字段，不会回传给服务端。
    """

    def _convert_chunk_to_generation_chunk(
        self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None
    ):
        gen = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen is not None and (choices := chunk.get("choices")):
            reasoning = (choices[0].get("delta") or {}).get("reasoning_content")
            if reasoning:
                gen.message.additional_kwargs["reasoning_content"] = reasoning
        return gen


def get_default_model_name() -> str:
    """延迟读取环境变量，确保 config.json 的 env 已注入"""
    return os.getenv("LLM_MODEL_NAME", "qwen3-max")


def detect_protocol(model_name: str) -> Protocol:
    """按模型名判定客户端协议"""
    name = (model_name or "").lower()
    if "claude" in name or "anthropic" in name or "minimax" in name:
        return "anthropic"
    return "openai"


def _is_qwen_dialect(model_name: str) -> bool:
    """按模型名判定 DashScope/百炼方言（与 detect_protocol 同类：名字 → 厂商）。"""
    return "qwen" in (model_name or "").lower()


def allowed_levels(model_name: str) -> tuple[str, ...]:
    """该模型可设的思考档位集合（UI 下发与校验共用同一份）。"""
    from lumi.models.catalog import lookup

    return levels_for(lookup(model_name), model_name)


def levels_for(entry: "ModelEntry | None", model_name: str) -> tuple[str, ...]:
    """目录条目 → 可设档位集合。

    收 entry 而非只收名字，是为了描述**目录条目本身**（如手动指定映射时的候选
    列表）时不必再把 id 当模型名解析一遍——那一圈会经过别名表，算出别的条目的档位。
    model_name 仅用于协议方言判定（Anthropic 与 OpenAI 的 auto 语义不同）。

    各形态的 auto 语义不同（见 docs/architecture/thinking.md）：
    - none 型（无思考/常开/未匹配/无缓存）→ 仅 auto（不传参数）
    - toggle 型 → 仅 on/off（开关模型只有两种行为；未设置时不传参数）
    - anthropic effort 型 → auto 即 adaptive（开思考、深度自适应），
      原生档位指定深度，off 关闭（不传 thinking）
    - openai effort 型 → auto = 不传（推理模型默认即思考），原生档位
      （含 none 等关闭值）原样列出
    """
    if entry is None or entry.control == "none":
        return ("auto",)
    # 各形态的原生档位集合；末尾统一追加 Lumi 合成顶档 ultra（思考拉满 + 解锁 workflow
    # 编排）。仅对有思考能力的模型提供——none 型上面已返回，不渲染 ultra。
    if entry.control == "toggle":
        base = ("on", "off")
    elif detect_protocol(model_name) == "anthropic":
        base = ("auto", *entry.values, "off")
    else:
        extra = ("off",) if entry.has_toggle and "off" not in entry.values else ()
        base = ("auto", *entry.values, *extra)
    return (*base, "ultra")


def _native_max_level(model_name: str) -> str:
    """该模型最高原生思考档（ultra 的委派目标）。effort 型取 ``values`` 末档
    （models.dev 升序，如 Claude→max / GPT→high），toggle 型为 on，无能力为 auto。"""
    from lumi.models.catalog import lookup

    entry = lookup(model_name)
    if entry is None or entry.control == "none":
        return "auto"
    if entry.control == "toggle":
        return "on"
    return entry.values[-1] if entry.values else "auto"


def _thinking_toggle(model_name: str) -> bool:
    """该模型是否存在关思考的通道（见 catalog 的 ``toggle_anywhere``）。"""
    from lumi.models.catalog import lookup

    entry = lookup(model_name)
    return entry is not None and entry.toggle_anywhere


def effort_params(model_name: str, level: str) -> dict:
    """思考档位 → 协议参数（唯一映射点）。

    档位值来自 models.dev（model_catalog），是各模型原生值，选什么发什么，
    不存在档位翻译。level 不在该模型 allowed_levels 内（能力数据更新后失效）
    时静默回退 auto（不传任何参数，零风险）。

    写法按协议 + 控制类型：
    - anthropic（仅 effort 型）：auto/档位 → adaptive thinking
      （auto 不指定深度=自适应；档位附 output_config.effort），
      display=summarized 否则拿不到思考文本；off → 不传（API 默认不思考）
    - openai effort 型：auto → 不传（模型默认）；档位 → reasoning_effort
      原样透传（含 none/xhigh），钉死 use_responses_api=False 防隐式路由
    - toggle 的 on/off：按厂商方言分支——Qwen（DashScope/百炼）用扁平
      enable_thinking 布尔；DeepSeek / MiMo 系用 thinking.type enabled/disabled
    """
    allowed = allowed_levels(model_name)

    # effort 型 qwen 的 allowed_levels 无 "off"（UI 不给 Off），会被下面的门控挡掉——但
    # force_no_thinking 的内部链必须真能关掉思考，故在门控前直通，且仅限确有关思考通道
    # 者（thinking-only 的 qwen 传 false 会被 API 拒绝）。原因见 thinking.md 的方言节。
    if level == "off" and _is_qwen_dialect(model_name) and _thinking_toggle(model_name):
        return {"extra_body": {"enable_thinking": False}}

    if level != "auto" and level not in allowed:
        return {}

    # ultra 非协议值，是 Lumi 顶档：思考层面 = 委派给该模型最高原生档（唯一别名点，
    # 下游协议分支无需感知 ultra）。「解锁 workflow」由轮内提醒承载，与思考参数无关。
    if level == "ultra":
        return effort_params(model_name, _native_max_level(model_name))

    if detect_protocol(model_name) == "anthropic":
        if level == "off" or allowed == ("auto",):
            return {}  # 关闭 = 不传 thinking（API 默认不思考）；无能力模型同
        adaptive = {"thinking": {"type": "adaptive", "display": "summarized"}}
        if level in ("auto", "on"):
            return adaptive  # auto/on 即 adaptive，深度交给 API 默认
        return {**adaptive, "output_config": {"effort": level}}

    if level == "auto":
        return {}
    if level in ("on", "off"):
        enabled = level == "on"
        # Qwen3（DashScope/百炼）方言：扁平 enable_thinking 布尔；其余（DeepSeek/MiMo）
        # 走 thinking.type。错误码 InternalError.Algo.* 即 DashScope。
        if _is_qwen_dialect(model_name):
            return {"extra_body": {"enable_thinking": enabled}}
        state = "enabled" if enabled else "disabled"
        return {"extra_body": {"thinking": {"type": state}}}
    return {"reasoning_effort": level, "use_responses_api": False}


def rejects_forced_tool_choice(model_name: str) -> bool:
    """该模型是否无法承受强制 tool_choice（required / 指定工具）。

    DashScope（qwen）在思考模式下一律拒绝强制 tool_choice（实测 400）；而 thinking-only
    的 qwen 思考模型——有思考能力却无任何 toggle 通道，如 qwen3.8-max-preview 的
    enable_thinking 被 API 限定为 True——永远处在思考模式里，强制必然撞上。无思考能力的
    qwen 不在此列（它压根不进思考模式）。消费方是 chain.structured_output 的软引导降级。
    """
    return (
        _is_qwen_dialect(model_name)
        and allowed_levels(model_name) != ("auto",)  # 有思考能力
        and not _thinking_toggle(model_name)  # 且关不掉
    )


def create_llm(
    model_name: str | None = None,
    use_cache: bool = True,
    apply_effort: bool = False,
    force_no_thinking: bool = False,
    effort: str | None = None,
    provider: str = "",
    **llm_params,
) -> ChatAnthropic | ChatOpenAI:
    """创建 LLM 实例（同参数命中缓存）。

    连接解析：显式传入 base_url / api_key 时原样使用；否则由
    provider_store.resolve() 解析供应商 profile 注入连接——provider 非空时
    精确取该 profile（同名模型跨 profile 不串味），否则按 model_name 反查
    （model_name 为 None 时用 active 模型，无 active 回退 env 默认）。

    思考档位注入是显式 opt-in（apply_effort=True，仅主对话链使用）：
    默认不注入任何思考参数——摘要、结构化提取、连通性测试等内部链
    保持干净，无需各自对冲。
    force_no_thinking=True 则主动**关闭**思考（注入 off 档位）：强制
    tool_choice 的链（结构化输出 / 受迫工具调用）与思考模式不兼容，对
    默认常开思考的模型（如 qwen toggle 型）必须显式关闭，仅「不注入」不够。
    catalog 门控天然安全——无思考能力 / Anthropic 默认不思考的模型 off 即 {}。
    effort（非 None）显式覆盖档位、绕过 profile：IM 渠道会话据此独立配置思考模式，
    而不改动全局 provider_store 的 profile；None 时沿用 resolve() 解析出的 profile 档位。
    参数优先级：config.json llm_params < effort 档位 < 调用方 llm_params；
    无内置调参默认，未指定的参数交给 SDK 默认值。
    """
    from lumi.models import provider_store
    from lumi.utils.read_config import get_config

    level = "auto"
    if "base_url" in llm_params or "api_key" in llm_params:
        model_name = model_name or get_default_model_name()
    else:
        resolved = provider_store.resolve(model_name, provider)
        model_name = resolved.model
        level = resolved.effort
        if resolved.base_url:
            llm_params["base_url"] = resolved.base_url
        if resolved.api_key:
            llm_params["api_key"] = resolved.api_key
    # 渠道等显式档位覆盖 profile（绕过全局，不改 provider_store）
    if effort is not None:
        level = effort

    protocol = detect_protocol(model_name)
    config_params = get_config().config.llm_params.get_params_for_model_type(protocol)
    if apply_effort:
        level_params = effort_params(model_name, level)
    elif force_no_thinking:
        level_params = effort_params(model_name, "off")
    else:
        level_params = {}
    final_params = {
        **config_params,
        **level_params,
        "model": model_name,
        **llm_params,
    }
    if protocol == "openai":
        # 功能性标志而非调参：流式响应里携带 token 用量统计
        final_params.setdefault("stream_usage", True)
    # 思考开启时采样参数互斥（Anthropic 直接 400，OpenAI 推理模型不支持）
    if final_params.get("thinking") or final_params.get("reasoning_effort"):
        for key in ("temperature", "top_p", "top_k"):
            final_params.pop(key, None)

    cache_key = (
        json.dumps(final_params, sort_keys=True, default=str) if use_cache else None
    )
    if cache_key and cache_key in _cache:
        return _cache[cache_key]

    cls = ChatAnthropic if protocol == "anthropic" else DialectChatOpenAI
    logger.debug(f"创建 {cls.__name__} 模型: {model_name}")
    llm = cls(**final_params)
    if cache_key:
        _cache[cache_key] = llm
    return llm

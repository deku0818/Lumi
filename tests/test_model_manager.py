"""model_manager / model_catalog 纯函数单元测试：协议判定与思考档位映射。"""

from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableLambda

from lumi.models.catalog import ModelEntry, _build_index
from lumi.models.manager import (
    allowed_levels,
    detect_protocol,
    effort_params,
    rejects_forced_tool_choice,
)


def _entry(
    mid: str,
    control: str,
    values: tuple = (),
    has_toggle: bool = False,
    toggle_anywhere: bool = False,
):
    return ModelEntry(
        id=mid,
        context_length=128000,
        control=control,
        values=values,
        has_toggle=has_toggle,
        # 并集不可能窄于择优条目自身
        toggle_anywhere=has_toggle or toggle_anywhere,
    )


@pytest.fixture
def catalog(monkeypatch):
    """注入假 models.dev 索引（隔离磁盘缓存与网络）。"""
    index = {
        "claude-opus-4-6": _entry(
            "claude-opus-4-6", "effort", ("low", "medium", "high", "max")
        ),
        "gpt-5.2": _entry("gpt-5.2", "effort", ("none", "low", "medium", "high")),
        "mimo-v2.5-pro": _entry("mimo-v2.5-pro", "toggle", (), True),
        "deepseek-v4-pro": _entry("deepseek-v4-pro", "effort", ("high", "max"), True),
        "qwen3.6-plus": _entry("qwen3.6-plus", "toggle", (), True),
        # 可关思考的 effort 型 qwen：择优条目只报了档位（has_toggle=False，故
        # allowed_levels 无 off），但别处 provider 报过 toggle → toggle_anywhere=True
        "qwen3.7-plus": _entry(
            "qwen3.7-plus", "effort", ("low", "medium", "high"), toggle_anywhere=True
        ),
        # thinking-only qwen：任何 provider 都没有 toggle 通道，DashScope 限定
        # enable_thinking=True
        "qwen3.8-max-preview": _entry(
            "qwen3.8-max-preview", "effort", ("low", "medium", "xhigh")
        ),
        "qwen3-max": _entry("qwen3-max", "none"),
        # 非 qwen 的 effort 型，某聚合 provider 报过 toggle（toggle_anywhere=True）——
        # 关思考的方言只对 qwen 直通，它的档位与 off 行为都不该受影响
        "o4-mini": _entry(
            "o4-mini", "effort", ("low", "medium", "high"), toggle_anywhere=True
        ),
    }
    monkeypatch.setattr("lumi.models.catalog._index", index)
    monkeypatch.setattr("lumi.models.catalog._lookup_memo", {})


def test_detect_protocol():
    assert detect_protocol("claude-opus-4-6") == "anthropic"
    assert detect_protocol("us.anthropic.claude-sonnet-4-5") == "anthropic"
    assert detect_protocol("MiniMax-M2") == "anthropic"
    assert detect_protocol("qwen3-max") == "openai"
    assert detect_protocol("") == "openai"


def test_allowed_levels(catalog):
    # anthropic effort 型：auto(=adaptive) + 原生档位 + off + ultra（Lumi 顶档）
    assert allowed_levels("claude-opus-4-6") == (
        "auto",
        "low",
        "medium",
        "high",
        "max",
        "off",
        "ultra",
    )
    # toggle 型：on/off + ultra
    assert allowed_levels("mimo-v2.5-pro") == ("on", "off", "ultra")
    # openai effort + toggle 并存且无原生关闭值 → 附加 off + ultra
    assert allowed_levels("deepseek-v4-pro") == ("auto", "high", "max", "off", "ultra")
    # 无思考 / 未匹配 / 无缓存 → 仅 auto（不渲染 ultra，无思考子菜单可挂）
    assert allowed_levels("qwen3-max") == ("auto",)
    assert allowed_levels("unknown-model") == ("auto",)


def test_effort_ultra_delegates_to_native_max(catalog):
    # ultra = Lumi 顶档：思考层面委派给该模型最高原生档（与直接选最高档等价）
    assert effort_params("claude-opus-4-6", "ultra") == effort_params(
        "claude-opus-4-6", "max"
    )
    assert effort_params("gpt-5.2", "ultra") == effort_params("gpt-5.2", "high")
    # toggle 型最高 = on
    assert effort_params("mimo-v2.5-pro", "ultra") == effort_params(
        "mimo-v2.5-pro", "on"
    )
    # 无思考模型不提供 ultra → 失效档位回退空（不传参数）
    assert effort_params("qwen3-max", "ultra") == {}


def test_effort_auto_semantics(catalog):
    # anthropic effort 型的 auto = adaptive（开思考、深度自适应）
    assert effort_params("claude-opus-4-6", "auto") == {
        "thinking": {"type": "adaptive", "display": "summarized"}
    }
    # openai / toggle / 无思考模型的 auto = 不传任何参数
    assert effort_params("gpt-5.2", "auto") == {}
    assert effort_params("mimo-v2.5-pro", "auto") == {}
    assert effort_params("qwen3-max", "auto") == {}


def test_effort_invalid_level_falls_back_to_auto(catalog):
    # 不在该模型 allowed_levels 内（能力数据更新后失效）→ 不传任何参数
    assert effort_params("qwen3-max", "high") == {}
    assert effort_params("mimo-v2.5-pro", "high") == {}


def test_effort_anthropic(catalog):
    params = effort_params("claude-opus-4-6", "max")
    assert params["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert params["output_config"] == {"effort": "max"}
    # off = 不传 thinking（API 默认不思考）
    assert effort_params("claude-opus-4-6", "off") == {}


def test_effort_openai_passthrough(catalog):
    # 原生值原样透传，不做档位翻译（含 none）
    assert effort_params("gpt-5.2", "none")["reasoning_effort"] == "none"
    params = effort_params("gpt-5.2", "high")
    assert params == {"reasoning_effort": "high", "use_responses_api": False}


def test_effort_toggle_on_off(catalog):
    assert effort_params("mimo-v2.5-pro", "off") == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }
    assert effort_params("mimo-v2.5-pro", "on") == {
        "extra_body": {"thinking": {"type": "enabled"}}
    }
    # effort+toggle 并存的 off 同样走 toggle 写法
    assert effort_params("deepseek-v4-pro", "off") == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }
    # Qwen（DashScope）方言：扁平 enable_thinking 布尔，而非 thinking.type
    assert effort_params("qwen3.6-plus", "off") == {
        "extra_body": {"enable_thinking": False}
    }
    assert effort_params("qwen3.6-plus", "on") == {
        "extra_body": {"enable_thinking": True}
    }


def test_effort_qwen_off_dialect_and_thinking_only(catalog):
    # 可关思考的 effort 型 qwen：off 不在 allowed_levels（UI 不给 Off），但
    # force_no_thinking 的内部链须真能关掉思考 → 门控前直通 DashScope 方言
    assert "off" not in allowed_levels("qwen3.7-plus")
    assert effort_params("qwen3.7-plus", "off") == {
        "extra_body": {"enable_thinking": False}
    }
    # 非 off 档位不受影响：high 仍走 reasoning_effort 透传
    assert effort_params("qwen3.7-plus", "high") == {
        "reasoning_effort": "high",
        "use_responses_api": False,
    }
    # thinking-only qwen（无 toggle）：off 被门控挡掉，不注入 API 拒绝的
    # enable_thinking=false（DashScope 限定其只能为 True）
    assert "off" not in allowed_levels("qwen3.8-max-preview")
    assert effort_params("qwen3.8-max-preview", "off") == {}
    # 非 qwen 的 effort 型 off 仍返 {}（不误伤 o3/gpt 等）
    assert effort_params("gpt-5.2", "off") == {}
    # 无思考能力的 qwen（control=none）off 仍返 {}——不注入它可能不认的 enable_thinking
    assert allowed_levels("qwen3-max") == ("auto",)
    assert effort_params("qwen3-max", "off") == {}


def test_rejects_forced_tool_choice(catalog):
    # thinking-only qwen：思考关不掉，DashScope 在思考模式下拒绝强制 tool_choice
    assert rejects_forced_tool_choice("qwen3.8-max-preview") is True
    # 可关思考的 qwen：关掉思考即可强制
    assert rejects_forced_tool_choice("qwen3.7-plus") is False
    assert rejects_forced_tool_choice("qwen3.6-plus") is False
    # 无思考能力的 qwen / 非 qwen（无 toggle 的 gpt 推理模型支持强制）不受影响
    assert rejects_forced_tool_choice("qwen3-max") is False
    assert rejects_forced_tool_choice("gpt-5.2") is False


def test_toggle_anywhere_does_not_leak_off_to_other_vendors(catalog):
    """``toggle_anywhere`` 只服务「能否关思考」判定，不外溢到档位枚举与他厂方言。

    并集若落进 has_toggle，几十个 openai 协议 effort 模型（o4-mini / gemini-flash
    等，某些聚合 provider 报了 toggle）会凭空多出 Off 档，且 force_no_thinking 会
    对它们注入 DeepSeek 系的 thinking.type —— 端点不认即 400。
    """
    assert allowed_levels("o4-mini") == ("auto", "low", "medium", "high", "ultra")
    assert effort_params("o4-mini", "off") == {}


def test_build_index_picks_one_provider_but_unions_toggle():
    """择优只取单个 provider 的档位写法，唯 toggle_anywhere 跨 provider 汇总。

    每个 provider 只上报自己暴露的控制项：qwen3.7-plus 在一处只报 effort 档位、
    另一处只报 toggle。混搭档位会造出没有端点实现的组合（如把别处的 none 并进
    Claude 的档位表），而丢掉 toggle 又会误判为 thinking-only——故只汇总后者。
    """
    raw = {
        "prov-effort": {
            "models": {
                "qwen3.7-plus": {
                    "reasoning": True,
                    "reasoning_options": [
                        {"type": "effort", "values": ["low", "medium", "high"]}
                    ],
                    "limit": {"context": 1000000},
                }
            }
        },
        "prov-toggle": {
            "models": {
                "qwen3.7-plus": {
                    "reasoning": True,
                    "reasoning_options": [{"type": "toggle"}],
                    "limit": {"context": 262144},
                }
            }
        },
        "prov-none": {
            "models": {"qwen3.7-plus": {"reasoning": False, "limit": {"context": 1024}}}
        },
    }
    entry = _build_index(raw)["qwen3.7-plus"]
    assert entry.control == "effort"
    assert entry.values == ("low", "medium", "high")  # 择优条目原样，不混搭
    assert entry.has_toggle is False  # 该条目自身没报 toggle → UI 不给 Off
    assert entry.toggle_anywhere is True  # 别处报过 → 存在关思考的通道
    assert entry.context_length == 1000000


def test_create_llm_effort_override(catalog, monkeypatch):
    """create_llm(effort=X) 覆盖 profile 档位；effort=None 沿用 resolve() 解析出的档位。

    这是 IM 渠道独立配置思考档位的机制：绕过 provider_store 的 profile.effort，
    不改全局。这里拦截 effort_params 记录实际生效的 level，并把 LLM 构造桩掉。
    """
    from lumi.models import manager
    from lumi.models.provider_store import ResolvedModel

    # profile 档位为 low（resolve 返回）；连接留空走 resolve 分支
    monkeypatch.setattr(
        "lumi.models.provider_store.resolve",
        lambda name=None: ResolvedModel("claude-opus-4-6", "", "", "low"),
    )
    seen: list[str] = []
    monkeypatch.setattr(
        manager, "effort_params", lambda model, level: seen.append(level) or {}
    )
    monkeypatch.setattr(manager, "ChatAnthropic", lambda **kw: kw)
    monkeypatch.setattr(manager, "DialectChatOpenAI", lambda **kw: kw)

    manager.create_llm(
        "claude-opus-4-6", use_cache=False, apply_effort=True, effort="high"
    )
    assert seen[-1] == "high"  # 覆盖盖过 profile 的 low

    manager.create_llm(
        "claude-opus-4-6", use_cache=False, apply_effort=True, effort=None
    )
    assert seen[-1] == "low"  # 不覆盖 → 沿用 resolve() 的 low


def test_structured_output_softens_tool_choice_for_thinking_only(catalog, monkeypatch):
    """thinking-only 模型的结构化链降级为软引导（tool_choice=auto），其余保持默认强制。

    with_structured_output(function_calling) 默认注入 tool_choice=<工具名>，
    DashScope 在思考模式下对此一律 400，而思考又关不掉。
    """
    from pydantic import BaseModel

    from lumi.models import chain as chain_module

    class _Schema(BaseModel):
        ok: bool

    seen: dict[str, dict] = {}

    class FakeLLM:
        def __init__(self, model_name: str):
            self.model_name = model_name

        def with_structured_output(self, structure, **kwargs):
            seen[self.model_name] = kwargs
            return RunnableLambda(lambda x: x)

    monkeypatch.setattr(
        chain_module, "create_llm", lambda model_name=None, **kw: FakeLLM(model_name)
    )

    chain_module.structured_output("{q}", _Schema, model_name="qwen3.8-max-preview")
    chain_module.structured_output("{q}", _Schema, model_name="qwen3.7-plus")

    assert seen["qwen3.8-max-preview"]["tool_choice"] == "auto"
    assert "tool_choice" not in seen["qwen3.7-plus"]


def test_soft_tool_choice_rejects_prose_answer():
    """软引导下模型可以不调工具，解析器返回 None——须显式抛错而非交给调用方。

    调用方拿 None 会 AttributeError（titler 的 result.title / 判官的 verdict.ok），
    抛错才能让分类器 fail-closed 转人工审批、其余照常上抛。
    """
    from lumi.models.chain import _require_structured_result

    sentinel = object()
    assert _require_structured_result(sentinel) is sentinel
    with pytest.raises(ValueError, match="未调用结构化输出工具"):
        _require_structured_result(None)

"""model_manager / model_catalog 纯函数单元测试：协议判定与思考档位映射。"""

from __future__ import annotations

import pytest

from lumi.models.catalog import ModelEntry
from lumi.models.manager import allowed_levels, detect_protocol, effort_params


def _entry(mid: str, control: str, values: tuple = (), has_toggle: bool = False):
    return ModelEntry(
        id=mid,
        context_length=128000,
        control=control,
        values=values,
        has_toggle=has_toggle,
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
        # effort 型 qwen（如 qwen3.7-plus）：有 low/medium/high 档位但无 toggle，
        # 故 allowed_levels 不含 off——回归 force_no_thinking 关思考需特殊直通
        "qwen3.7-plus": _entry("qwen3.7-plus", "effort", ("low", "medium", "high")),
        "qwen3-max": _entry("qwen3-max", "none"),
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


def test_effort_qwen_effort_type_off_still_disables_thinking(catalog):
    # 回归：effort 型 qwen（off 不在 allowed_levels）的 force_no_thinking 必须真关思考，
    # 否则强制 tool_choice 的结构化输出链在 DashScope thinking mode 下 400。
    assert "off" not in allowed_levels("qwen3.7-plus")  # 门控会挡掉 off
    assert effort_params("qwen3.7-plus", "off") == {
        "extra_body": {"enable_thinking": False}
    }
    # 非 off 档位不受影响：high 仍走 reasoning_effort 透传
    assert effort_params("qwen3.7-plus", "high") == {
        "reasoning_effort": "high",
        "use_responses_api": False,
    }
    # 非 qwen 的 effort 型 off 仍返 {}（不误伤 o3/gpt 等）
    assert effort_params("gpt-5.2", "off") == {}
    # 无思考能力的 qwen（control=none）off 仍返 {}——不注入它可能不认的 enable_thinking
    assert allowed_levels("qwen3-max") == ("auto",)
    assert effort_params("qwen3-max", "off") == {}


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

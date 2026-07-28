"""模型供应商 profile CRUD（从 AgentBridge 拆出的职责子模块）。

逻辑逐字照搬自原 AgentBridge；持 bridge 反向引用以读写 model_name / 应用 active。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage

from lumi.models import provider_store

if TYPE_CHECKING:
    from lumi.gateway.bridge.core import AgentBridge


class ProviderService:
    """供应商 profile 的列表 / 切换 / 增删与连通性测试。"""

    def __init__(self, bridge: AgentBridge) -> None:
        self._bridge = bridge

    def apply_active(self) -> None:
        """把当前 active 模型应用到运行时 context（下一轮 call_model 生效）。

        连接（base_url / api_key）不进 context，由 create_llm 按模型名解析。
        """
        b = self._bridge
        if b._context is None:
            return
        resolved = provider_store.resolve()
        b._context.model_name = resolved.model
        b._context.provider = resolved.provider
        b.model_name = resolved.model

    @staticmethod
    def provider_list() -> dict:
        """供应商列表。每个模型附思考能力（来自 models.dev）与当前档位：

        thinking[model] = {"control": "none|effort|toggle", "levels": [...],
                           "effort": "<当前档位>"}
        control 决定前端渲染形态（none 不渲染 / effort 档位列表 / toggle 开关），
        前端零推导；levels 为可设档位（校验同源）。

        另附三组限制数据。context / max_tokens 与 save_provider 的输入**同名同义**
        （都是用户覆盖表），故列表结果可原样回传不会串味：
        - context_window[model] = **生效**上下文窗口（覆盖 > 探测），上下文环的分母；
        - context[model] / max_tokens[model] = 用户填的覆盖值（0 = 没填 = 跟随探测）；
        - probe[model] = {context, max_tokens} models.dev 探测值（0 = 未探测到）。
        顶层 fallback = 两者皆无时后端真正会用的兜底值，让 UI 显示的数与实跑一致。
        """
        from lumi.models.catalog import context_window, lookup, max_output_tokens
        from lumi.models.manager import allowed_levels
        from lumi.utils.read_config import get_config

        profiles, active = provider_store.load()

        def thinking_of(m: str) -> dict:
            entry = lookup(m)
            return {
                "control": entry.control if entry else "none",
                "levels": list(allowed_levels(m)),
            }

        def profile_payload(p: provider_store.ProviderProfile) -> dict:
            # 生效值走 provider_store.limits（取值链的唯一实现），而非 resolve()：
            # resolve 按模型名反查会偏向 active profile，同名模型存在于多个 profile
            # 时会把别人的覆盖值贴到这一行，且每个模型多一次 lumi.json 读盘。
            probe = {
                m: {"context": context_window(m), "max_tokens": max_output_tokens(m)}
                for m in p.models
            }
            eff = {m: provider_store.limits(p, m) for m in p.models}
            return {
                "id": p.id,
                "name": p.name,
                "base_url": p.base_url,
                "api_key": p.api_key,
                "models": list(p.models),
                "thinking": {
                    m: {**thinking_of(m), "effort": p.effort.get(m, "auto")}
                    for m in p.models
                },
                "context_window": {m: eff[m][0] for m in p.models},
                "context": {m: p.context.get(m, 0) for m in p.models},
                "max_tokens": {m: p.max_tokens.get(m, 0) for m in p.models},
                "probe": probe,
            }

        cfg = get_config().config
        pointers = provider_store.get_pointers()
        return {
            "profiles": [profile_payload(p) for p in profiles],
            "active": active,
            "classifier": pointers["classifier"],
            "titler": pointers["titler"],
            "fallback": {
                "context": cfg.token.context_length,
                "max_tokens": cfg.agents.max_tokens,
            },
        }

    def set_effort(self, provider_id: str, model: str, level: str) -> dict:
        """设置某 (provider, model) 的思考档位（持久化，下一轮 LLM 调用生效）。

        Raises:
            ValueError: provider/model 不存在或档位不在该模型能力内。
        """
        if provider_store.set_effort(provider_id, model, level) is None:
            raise ValueError(f"无法设置思考档位: {provider_id}/{model} → {level}")
        return {"effort": level}

    def list_providers(self) -> dict:
        """列出全部供应商 profile（含 models 列表）及 active / classifier / titler 指针。"""
        return self.provider_list()

    def set_classifier(self, provider_id: str, model: str) -> dict:
        """设置/清除 auto 审批分类器模型指针（持久化，下一轮 auto 裁决生效）。

        provider/model 均空或指向不存在 → 清除（回退会话模型）。无需重启运行时：
        auto_classify 每次裁决时按指针即时解析连接。
        """
        return {
            "classifier": provider_store.set_pointer("classifier", provider_id, model)
        }

    def set_titler(self, provider_id: str, model: str) -> dict:
        """设置/清除会话标题生成模型指针（持久化，下一次标题生成生效）。"""
        return {"titler": provider_store.set_pointer("titler", provider_id, model)}

    async def test_provider(self, base_url: str, api_key: str, model: str) -> dict:
        """用给定连接对模型发一个最小请求验证可达性。

        短超时（15s）+ 不缓存 + 不重试，连不上的地址会快速失败而非干等。
        返回 {ok: bool, error?: str, latency_ms?: int}。
        """
        from lumi.models.manager import create_llm

        if not model:
            return {"ok": False, "error": "未指定模型"}

        kwargs: dict = {"timeout": 15, "max_tokens": 16, "max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        try:
            llm = create_llm(model_name=model, use_cache=False, **kwargs)
            t0 = time.monotonic()
            await llm.ainvoke([HumanMessage(content="ping")])
            return {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def set_provider(self, provider_id: str, model: str) -> dict:
        """切换 active 到 (provider, model)：持久化 + 立即应用（下一轮生效）。

        Raises:
            ValueError: provider 或 model 不存在（如前端列表已过期）——
                静默返回旧 active 会让调用方误以为切换成功。
        """
        if provider_store.set_active(provider_id, model) is None:
            raise ValueError(f"切换失败：供应商或模型不存在（{provider_id} / {model}）")
        self.apply_active()
        return {"active": provider_store.load()[1], "model": self._bridge.model_name}

    def save_provider(self, profile: dict) -> dict:
        """新增或更新一个 profile；active 可能因其模型增删失效，故重新应用归位。"""
        provider_store.upsert(profile)
        self.apply_active()
        return self.provider_list()

    def delete_provider(self, provider_id: str) -> dict:
        """删除一个 profile；删的是 active 时回退到新的 active（或默认）。"""
        provider_store.delete(provider_id)
        self.apply_active()
        return self.provider_list()

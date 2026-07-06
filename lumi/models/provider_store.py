"""模型供应商配置（profile）持久化 — 用户自定义的 base_url / api_key / 多个 model。

一个 profile = 一套连接（name、base_url、api_key）+ 该连接下的一组模型 models
+ 按模型的思考档位 effort（model → level，只存非 auto）。
协议（OpenAI / Anthropic 客户端）仍由 model 名自动判定（见 manager.detect_protocol）。
active 指向「某 profile 下的某个 model」。存储为 ~/.lumi/lumi.json 的 "providers" 分区（含密钥，整体 chmod 600）：

    {"active": {"provider": "<id>", "model": "<model>"},
     "classifier": {"provider": "<id>", "model": "<model>"},   # auto 审批分类器模型，可缺省
     "titler": {"provider": "<id>", "model": "<model>"},       # 会话标题生成模型，可缺省
     "profiles": [{"id","name","base_url","api_key",
                   "models":["m1","m2"], "effort":{"m1":"high"}}, ...]}

classifier / titler 是独立于对话 active 的「用途指针」（auto 审批裁决 / 会话标题生成），
缺省/失效时回退会话 active 模型；经 get_pointer / resolve_pointer / set_pointer 读写。

无 textual 依赖，可在 headless 服务（lumi serve）中直接使用。
兼容旧格式（profile 用单个 "model" 字段、active 为字符串 id；
顶层全局 "effort" 字段已废弃，读取时忽略）。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace

from lumi.models.manager import allowed_levels, get_default_model_name
from lumi.utils.config import user_store

# active 选中项：provider id + 该 provider 下的某个 model
Active = dict  # {"provider": str, "model": str}
_EMPTY_ACTIVE: Active = {"provider": "", "model": ""}

# 用途指针：独立于对话 active 的按用途模型选择（存储键 = 指针名）
_POINTER_KINDS = ("classifier", "titler")


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    name: str
    base_url: str
    api_key: str
    models: tuple[str, ...]
    effort: dict[str, str] = field(default_factory=dict)
    """按模型的思考档位（model → level），只存非 auto。"""


def _coerce_profile(x: dict) -> ProviderProfile | None:
    """把一条原始记录转成 ProviderProfile，兼容旧的单 model 字段。"""
    if not isinstance(x, dict) or "id" not in x or "name" not in x:
        return None
    raw_models = x.get("models")
    if not raw_models and x.get("model"):  # 旧格式：单个 model
        raw_models = [x["model"]]
    models = tuple(
        m.strip() for m in (raw_models or []) if isinstance(m, str) and m.strip()
    )
    raw_effort = x.get("effort")
    if not isinstance(raw_effort, dict):
        raw_effort = {}
    effort = {
        m: lv for m, lv in raw_effort.items() if m in models and isinstance(lv, str)
    }
    return ProviderProfile(
        id=x["id"],
        name=x.get("name", ""),
        base_url=x.get("base_url", ""),
        api_key=x.get("api_key", ""),
        models=models,
        effort=effort,
    )


def _kept_pointer(profiles: list[ProviderProfile], pointer: object) -> dict:
    """规范化 (provider, model) 指针：指向真实存在的 profile+model 才保留，否则空 dict。"""
    ptr = pointer if isinstance(pointer, dict) else {}
    pid, model = ptr.get("provider", ""), ptr.get("model", "")
    prof = next((p for p in profiles if p.id == pid), None)
    return {"provider": pid, "model": model} if prof and model in prof.models else {}


def _normalize_active(profiles: list[ProviderProfile], active: dict) -> Active:
    """保证 active 指向真实存在的 (provider, model)；无效时回退到首个可用模型。"""
    kept = _kept_pointer(profiles, active)
    if kept:
        return kept
    for p in profiles:  # 回退：第一个有模型的 profile 的第一个模型
        if p.models:
            return {"provider": p.id, "model": p.models[0]}
    return dict(_EMPTY_ACTIVE)


def _read_data() -> dict:
    """读取 lumi.json 的 "providers" 分区一次；缺失/损坏返回空 dict。"""
    return user_store.read_section("providers", {})


def _parse(data: dict) -> tuple[list[ProviderProfile], Active, dict[str, dict]]:
    """从一次读盘的 data 解出 (profiles, 规范化 active, 规范化用途指针表)。"""
    profiles = [p for p in map(_coerce_profile, data.get("profiles", [])) if p]
    raw_active = data.get("active", {})
    if isinstance(raw_active, str):  # 旧格式：active 为 provider id
        raw_active = {"provider": raw_active, "model": ""}
    pointers = {k: _kept_pointer(profiles, data.get(k)) for k in _POINTER_KINDS}
    return profiles, _normalize_active(profiles, raw_active), pointers


def _load_all() -> tuple[list[ProviderProfile], Active, dict[str, dict]]:
    """一次读盘解出 (profiles, active, pointers)——mutator 用它避免为指针再读一次。"""
    return _parse(_read_data())


def load() -> tuple[list[ProviderProfile], Active]:
    """读取全部 profile 与规范化后的 active；缺失/损坏返回 ([], 空 active)。"""
    profiles, active, _ = _load_all()
    return profiles, active


def _save(
    profiles: list[ProviderProfile], active: dict, pointers: dict[str, dict]
) -> None:
    # pointers 由调用方传入（写操作前已随 _load_all 一并读出，无需 _save 再读盘）；
    # 按当前 profiles 规范化，profile/model 被删时自动清失效指针。
    payload = {
        "active": _normalize_active(profiles, active),
        "profiles": [{**asdict(p), "models": list(p.models)} for p in profiles],
    }
    for kind, ptr in pointers.items():
        norm = _kept_pointer(profiles, ptr)
        if norm:
            payload[kind] = norm
    user_store.write_section("providers", payload)


def set_effort(provider_id: str, model: str, level: str) -> str | None:
    """设置某 (provider, model) 的思考档位并持久化；非法时返回 None。

    合法档位 = model_catalog.allowed_levels(model)；auto 永远合法
    且即删除记录（恢复"未设置=不传参数"，只存非 auto）。
    """
    if level != "auto" and level not in allowed_levels(model):
        return None
    profiles, active, pointers = _load_all()
    prof = next((p for p in profiles if p.id == provider_id), None)
    if prof is None or model not in prof.models:
        return None
    effort = {m: lv for m, lv in prof.effort.items() if m != model}
    if level != "auto":
        effort[model] = level
    out = [p for p in profiles if p.id != provider_id]
    out.append(replace(prof, effort=effort))
    _save(out, active, pointers)
    return level


@dataclass(frozen=True)
class ResolvedModel:
    """模型 + 连接 + 思考档位的解析结果；base_url / api_key 为空表示用 env / SDK 默认。"""

    model: str
    base_url: str
    api_key: str
    effort: str = "auto"

    def conn_kwargs(self) -> dict:
        """非空的 base_url / api_key 连接参数，直接 ** 进 create_llm / chain 工厂。"""
        return {
            k: v
            for k, v in (("base_url", self.base_url), ("api_key", self.api_key))
            if v
        }


def resolve(model_name: str | None = None) -> ResolvedModel:
    """解析模型 + 连接 + 档位的单一事实源（一次读盘）。

    model_name 为 None → 用 active (profile, model)，无 active 回退 env 默认模型；
    指定 model_name → 反查包含它的 profile（active 优先），查不到则无连接覆盖。
    """
    profiles, active = load()
    if model_name is None:
        prof = next((p for p in profiles if p.id == active["provider"]), None)
        if prof and active["model"]:
            model = active["model"]
            return ResolvedModel(
                model, prof.base_url, prof.api_key, prof.effort.get(model, "auto")
            )
        return ResolvedModel(get_default_model_name(), "", "")

    for p in sorted(profiles, key=lambda p: p.id != active["provider"]):
        if model_name in p.models:
            return ResolvedModel(
                model_name, p.base_url, p.api_key, p.effort.get(model_name, "auto")
            )
    return ResolvedModel(model_name, "", "")


def resolve_vision() -> ResolvedModel | None:
    """解析视觉辅助模型 + 连接（来自 config.json 的 vision 配置）；未配 model → None。

    base_url / api_key 留空则反查 providers 分区里含该模型的 profile 连接（resolve）；
    仍查不到则连接为空（create_llm 用 env / SDK 默认）。档位恒 auto。
    """
    from lumi.utils.read_config import get_config

    cfg = get_config().config.vision
    if not cfg.model:
        return None
    if cfg.base_url or cfg.api_key:
        return ResolvedModel(cfg.model, cfg.base_url, cfg.api_key, "auto")
    return replace(resolve(cfg.model), effort="auto")


def get_pointer(kind: str) -> dict:
    """返回规范化后的用途指针 {provider, model}；未配/失效为空 dict。"""
    return get_pointers()[kind]


def get_pointers() -> dict[str, dict]:
    """一次读盘返回全部用途指针表（list_providers 一并下发两个指针时免重复读）。"""
    _, _, pointers = _parse(_read_data())
    return pointers


def resolve_pointer(kind: str) -> ResolvedModel:
    """解析某用途指针的模型 + 连接（按 provider id 精确取 base_url/api_key）。

    未配置或指针失效 → 回退会话 active 模型（= 不单独配置时的行为）。
    """
    profiles, _, pointers = _parse(
        _read_data()
    )  # 单次读盘，同时拿 profiles 与规范化指针
    ptr = pointers[kind]
    if not ptr:
        return resolve()  # 跟随会话模型
    # ptr 已由 _parse 对同一 profiles 规范化，provider 必存在
    prof = next(p for p in profiles if p.id == ptr["provider"])
    return ResolvedModel(ptr["model"], prof.base_url, prof.api_key, "auto")


def set_pointer(kind: str, provider_id: str, model: str) -> dict:
    """设置/清除某用途指针并持久化，返回规范化后的指针（清除时为空 dict）。

    provider_id 与 model 均空 → 清除（跟随会话模型）；指向不存在的 (provider, model)
    → 同样视为清除（规范化丢弃，回退会话模型）。其余指针原样保留。
    """
    profiles, active, pointers = _load_all()
    ptr = {"provider": provider_id, "model": model} if provider_id and model else {}
    _save(profiles, active, {**pointers, kind: ptr})
    return _kept_pointer(profiles, ptr)


def upsert(profile: dict) -> ProviderProfile:
    """新增或按 id 更新一个 profile（models 为列表，去空去重保序）。

    思考档位不经此通道（set_effort 专用）：保留旧记录中仍存在的模型的档位。
    """
    profiles, active, pointers = _load_all()
    pid = profile.get("id") or uuid.uuid4().hex[:8]
    seen: set[str] = set()
    models = tuple(
        m
        for m in (s.strip() for s in profile.get("models", []) if isinstance(s, str))
        if m and not (m in seen or seen.add(m))
    )
    old = next((p for p in profiles if p.id == pid), None)
    effort = {m: lv for m, lv in old.effort.items() if m in models} if old else {}
    saved = ProviderProfile(
        id=pid,
        name=profile.get("name", ""),
        base_url=profile.get("base_url", ""),
        api_key=profile.get("api_key", ""),
        models=models,
        effort=effort,
    )
    out = [p for p in profiles if p.id != pid]
    out.append(saved)
    _save(out, active, pointers)  # active 由 _save 规范化（首条/失效自动归位）
    return saved


def delete(pid: str) -> None:
    profiles, active, pointers = _load_all()
    _save([p for p in profiles if p.id != pid], active, pointers)


def set_active(provider_id: str, model: str) -> Active | None:
    """切换 active 到 (provider, model)；provider 或 model 不存在返回 None。"""
    profiles, _, pointers = _load_all()
    prof = next((p for p in profiles if p.id == provider_id), None)
    if prof is None or model not in prof.models:
        return None
    active = {"provider": provider_id, "model": model}
    _save(profiles, active, pointers)
    return active

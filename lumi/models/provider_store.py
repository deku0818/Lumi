"""模型供应商配置（profile）持久化 — 用户自定义的 base_url / api_key / 多个 model。

一个 profile = 一套连接（name、base_url、api_key）+ 该连接下的一组模型 models
+ 按模型的思考档位 effort（model → level，只存非 auto）。
协议（OpenAI / Anthropic 客户端）仍由 model 名自动判定（见 manager.detect_protocol）。
active 指向「某 profile 下的某个 model」。存储为 ~/.lumi/providers.json（明文，chmod 600）：

    {"active": {"provider": "<id>", "model": "<model>"},
     "profiles": [{"id","name","base_url","api_key",
                   "models":["m1","m2"], "effort":{"m1":"high"}}, ...]}

无 textual 依赖，可在 headless 服务（lumi serve）中直接使用。
兼容旧格式（profile 用单个 "model" 字段、active 为字符串 id；
顶层全局 "effort" 字段已废弃，读取时忽略）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from lumi.models.manager import allowed_levels, get_default_model_name
from lumi.utils.atomic_io import atomic_write_json
from lumi.utils.config.global_manager import GLOBAL_CONFIG_DIR
from lumi.utils.logger import logger

# active 选中项：provider id + 该 provider 下的某个 model
Active = dict  # {"provider": str, "model": str}
_EMPTY_ACTIVE: Active = {"provider": "", "model": ""}


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    name: str
    base_url: str
    api_key: str
    models: tuple[str, ...]
    effort: dict[str, str] = field(default_factory=dict)
    """按模型的思考档位（model → level），只存非 auto。"""


def _path() -> Path:
    return GLOBAL_CONFIG_DIR / "providers.json"


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


def _normalize_active(profiles: list[ProviderProfile], active: dict) -> Active:
    """保证 active 指向真实存在的 (provider, model)；无效时回退到首个可用模型。"""
    by_id = {p.id: p for p in profiles}
    pid = active.get("provider", "") if isinstance(active, dict) else ""
    model = active.get("model", "") if isinstance(active, dict) else ""
    prof = by_id.get(pid)
    if prof and model in prof.models:
        return {"provider": pid, "model": model}
    for p in profiles:  # 回退：第一个有模型的 profile 的第一个模型
        if p.models:
            return {"provider": p.id, "model": p.models[0]}
    return dict(_EMPTY_ACTIVE)


def load() -> tuple[list[ProviderProfile], Active]:
    """读取全部 profile 与规范化后的 active；缺失/损坏返回 ([], 空 active)。"""
    path = _path()
    if not path.exists():
        return [], dict(_EMPTY_ACTIVE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("providers.json 读取失败: %s", path, exc_info=True)
        return [], dict(_EMPTY_ACTIVE)

    profiles = [p for p in map(_coerce_profile, data.get("profiles", [])) if p]
    raw_active = data.get("active", {})
    if isinstance(raw_active, str):  # 旧格式：active 为 provider id
        raw_active = {"provider": raw_active, "model": ""}
    return profiles, _normalize_active(profiles, raw_active)


def _save(profiles: list[ProviderProfile], active: dict) -> None:
    payload = {
        "active": _normalize_active(profiles, active),
        "profiles": [{**asdict(p), "models": list(p.models)} for p in profiles],
    }
    # 含 api_key，限制为仅本人可读写
    atomic_write_json(_path(), payload, mode=0o600)


def set_effort(provider_id: str, model: str, level: str) -> str | None:
    """设置某 (provider, model) 的思考档位并持久化；非法时返回 None。

    合法档位 = model_catalog.allowed_levels(model)；auto 永远合法
    且即删除记录（恢复"未设置=不传参数"，只存非 auto）。
    """
    if level != "auto" and level not in allowed_levels(model):
        return None
    profiles, active = load()
    prof = next((p for p in profiles if p.id == provider_id), None)
    if prof is None or model not in prof.models:
        return None
    effort = {m: lv for m, lv in prof.effort.items() if m != model}
    if level != "auto":
        effort[model] = level
    out = [p for p in profiles if p.id != provider_id]
    out.append(replace(prof, effort=effort))
    _save(out, active)
    return level


@dataclass(frozen=True)
class ResolvedModel:
    """模型 + 连接 + 思考档位的解析结果；base_url / api_key 为空表示用 env / SDK 默认。"""

    model: str
    base_url: str
    api_key: str
    effort: str = "auto"


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


def upsert(profile: dict) -> ProviderProfile:
    """新增或按 id 更新一个 profile（models 为列表，去空去重保序）。

    思考档位不经此通道（set_effort 专用）：保留旧记录中仍存在的模型的档位。
    """
    profiles, active = load()
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
    _save(out, active)  # active 由 _save 规范化（首条/失效自动归位）
    return saved


def delete(pid: str) -> None:
    profiles, active = load()
    _save([p for p in profiles if p.id != pid], active)


def set_active(provider_id: str, model: str) -> Active | None:
    """切换 active 到 (provider, model)；provider 或 model 不存在返回 None。"""
    profiles, _ = load()
    prof = next((p for p in profiles if p.id == provider_id), None)
    if prof is None or model not in prof.models:
        return None
    active = {"provider": provider_id, "model": model}
    _save(profiles, active)
    return active

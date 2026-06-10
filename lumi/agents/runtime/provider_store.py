"""模型供应商配置（profile）持久化 — 用户自定义的 base_url / api_key / 多个 model。

一个 profile = 一套连接（name、base_url、api_key）+ 该连接下的一组模型 models。
协议（OpenAI / Anthropic 客户端）仍由 model 名自动判定（见 model_manager.detect_model_type）。
active 指向「某 profile 下的某个 model」。存储为 ~/.lumi/providers.json（明文，chmod 600）：

    {"active": {"provider": "<id>", "model": "<model>"},
     "profiles": [{"id","name","base_url","api_key","models":["m1","m2"]}, ...]}

无 textual 依赖，可在 headless 服务（lumi serve）中直接使用。
兼容旧格式（profile 用单个 "model" 字段、active 为字符串 id）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from lumi.agents.runtime.checkpoint import _atomic_write_json
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
    return ProviderProfile(
        id=x["id"],
        name=x.get("name", ""),
        base_url=x.get("base_url", ""),
        api_key=x.get("api_key", ""),
        models=models,
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
    _atomic_write_json(_path(), payload, mode=0o600)


def get_active() -> tuple[ProviderProfile, str] | None:
    """返回当前 (profile, model)；无可用项时 None。"""
    profiles, active = load()
    prof = next((p for p in profiles if p.id == active["provider"]), None)
    if prof is None or not active["model"]:
        return None
    return prof, active["model"]


def upsert(profile: dict) -> ProviderProfile:
    """新增或按 id 更新一个 profile（models 为列表，去空去重保序）。"""
    profiles, active = load()
    pid = profile.get("id") or uuid.uuid4().hex[:8]
    seen: set[str] = set()
    models = tuple(
        m
        for m in (s.strip() for s in profile.get("models", []) if isinstance(s, str))
        if m and not (m in seen or seen.add(m))
    )
    saved = ProviderProfile(
        id=pid,
        name=profile.get("name", ""),
        base_url=profile.get("base_url", ""),
        api_key=profile.get("api_key", ""),
        models=models,
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

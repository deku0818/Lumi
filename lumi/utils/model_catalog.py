"""models.dev 模型目录 — 思考能力与上下文元数据的单一数据源。

数据：https://models.dev/api.json（MIT，社区维护，141 provider / 5000+ 模型）。
磁盘缓存 ~/.lumi/cache/models_dev.json（TTL 24h），启动时后台 refresh()；
离线沿用旧缓存；完全无缓存时能力未知 → 一律视为无思考控制（仅 Auto，安全降级）。

思考控制形态（reasoning_options 推导）：
- control="effort"：有原生档位枚举（values 原样展示、选什么发什么）
- control="toggle"：仅开/关
- control="none"：无思考，或常开型（reasoning=true 但无可控项）——均不渲染控制
budget_tokens 类型忽略（被 adaptive+effort 取代的旧范式）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from rapidfuzz import fuzz

from lumi.utils.config.global_manager import GLOBAL_CONFIG_DIR
from lumi.utils.logger import logger

_API_URL = "https://models.dev/api.json"
_CACHE_TTL = 24 * 3600
_MATCH_THRESHOLD = 80


@dataclass(frozen=True)
class ModelEntry:
    """单个模型的目录条目（不可变）。"""

    id: str
    context_length: int
    control: str  # "none" | "effort" | "toggle"
    values: tuple[str, ...]  # control="effort" 时的原生档位枚举
    has_toggle: bool  # effort 与 toggle 并存（UI 附加 Off 项）


def _cache_path() -> Path:
    return GLOBAL_CONFIG_DIR / "cache" / "models_dev.json"


def _derive_control(m: dict) -> tuple[str, tuple[str, ...], bool]:
    """从 models.dev 条目推导思考控制形态。"""
    if not m.get("reasoning"):
        return "none", (), False
    opts = m.get("reasoning_options") or []
    effort = next((o for o in opts if o.get("type") == "effort"), None)
    has_toggle = any(o.get("type") == "toggle" for o in opts)
    if effort and effort.get("values"):
        return "effort", tuple(effort["values"]), has_toggle
    if has_toggle:
        return "toggle", (), True
    return "none", (), False  # 常开型：无可控项，不渲染控制


def _entry_score(e: ModelEntry) -> int:
    """多 provider 同名条目择优：能力信息最完整者胜。"""
    return len(e.values) * 10 + (2 if e.control != "none" else 0) + e.has_toggle


def _build_index(raw: dict) -> dict[str, ModelEntry]:
    """全 provider 扁平化为 {model_id_lower: ModelEntry}，同名择优。"""
    index: dict[str, ModelEntry] = {}
    for prov in raw.values():
        for mid, m in (prov.get("models") or {}).items():
            if not isinstance(m, dict):
                continue
            control, values, has_toggle = _derive_control(m)
            limit = m.get("limit") or {}
            entry = ModelEntry(
                id=mid,
                context_length=int(limit.get("context") or 0),
                control=control,
                values=values,
                has_toggle=has_toggle,
            )
            key = mid.lower()
            if key not in index or _entry_score(entry) > _entry_score(index[key]):
                index[key] = entry
    return index


# 模块级缓存：磁盘 JSON 只解析一次；lookup 结果按查询名 memo
_index: dict[str, ModelEntry] | None = None
_lookup_memo: dict[str, ModelEntry | None] = {}


def _get_index() -> dict[str, ModelEntry]:
    global _index
    if _index is None:
        path = _cache_path()
        if not path.exists():
            _index = {}
            return _index
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            _index = _build_index(raw.get("data", {}))
        except (OSError, json.JSONDecodeError):
            # 损坏的缓存必须删除：refresh 的 TTL 只看 mtime，留着半截文件
            # 会让思考能力静默消失直到 TTL 过期
            logger.warning("[ModelCatalog] 缓存损坏，已删除等待重新拉取: %s", path)
            path.unlink(missing_ok=True)
            _index = {}
    return _index


def _invalidate() -> None:
    global _index
    _index = None
    _lookup_memo.clear()


def lookup(model_name: str) -> ModelEntry | None:
    """按模型名查目录：精确（小写）→ 模糊兜底；无缓存/未匹配返回 None。"""
    query = (model_name or "").lower().strip()
    if not query:
        return None
    if query in _lookup_memo:
        return _lookup_memo[query]

    index = _get_index()
    entry = index.get(query)
    if entry is None and index:
        best_score, best_key = 0.0, None
        for key in index:
            score = fuzz.token_set_ratio(query, key)
            if score > best_score:
                best_score, best_key = score, key
        if best_key and best_score >= _MATCH_THRESHOLD:
            entry = index[best_key]
    _lookup_memo[query] = entry
    return entry


async def refresh(force: bool = False) -> None:
    """拉取 models.dev 数据写入磁盘缓存（TTL 内跳过）；失败静默沿用旧缓存。"""
    from lumi.agents.runtime.checkpoint import _atomic_write_json

    path = _cache_path()
    if not force and path.exists():
        if time.time() - path.stat().st_mtime < _CACHE_TTL:
            return
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(_API_URL)
            resp.raise_for_status()
            raw = resp.json()
    except Exception:
        logger.warning("[ModelCatalog] models.dev 拉取失败，沿用旧缓存")
        return
    # 原子写：进程中途被杀不会留下半截 JSON
    _atomic_write_json(path, {"fetched_at": time.time(), "data": raw})
    _invalidate()
    logger.info("[ModelCatalog] models.dev 数据已更新")

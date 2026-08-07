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
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
from rapidfuzz import fuzz

from lumi.utils.atomic_io import atomic_write_json
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
    has_toggle: bool  # 择优条目自身 effort 与 toggle 并存（UI 附加 Off 项）
    toggle_anywhere: bool  # 任一 provider 报过 toggle = 该模型存在关思考的通道
    max_output: int = 0  # 单次输出 token 上限（0 = 目录未标注）


@dataclass(frozen=True)
class Match:
    """一次目录查询的结果 + **它是怎么来的**。

    kind 存在的意义是让界面能把「猜的」和「确定的」分开显示：模糊匹配是按字符
    相似度猜的，猜错时上下文窗口、输出上限、思考档位会一起取自另一个模型，而
    这件事此前完全静默。消费方只关心条目时用 ``lookup``（丢掉 kind）。
    """

    entry: ModelEntry | None
    # "manual" 用户指定 | "exact" 同名 | "fuzzy" 相似度猜 | "none" 没匹上
    # | "stale" 指定的条目已不在目录里（entry 是自动匹配的回落结果）
    kind: str


def _norm(name: str) -> str:
    """模型名 → 索引键。索引键、别名表键、memo 键、搜索串必须同一套规则，
    否则手动指定会在某一侧静默失配。"""
    return name.lower().strip()


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
    """全 provider 扁平化为 {model_id_lower: ModelEntry}，同名择优。

    择优只取**单个** provider 的条目：档位写法按 provider 而异，跨 provider 混搭
    会造出没有端点实现的档位组合。唯一跨 provider 汇总的是 ``toggle_anywhere``
    （见 docs/architecture/thinking.md「数据源」节）。
    """
    index: dict[str, ModelEntry] = {}
    toggle_keys: set[str] = set()
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
                toggle_anywhere=has_toggle,  # 单 provider 视角，下面按 key 汇总
                max_output=int(limit.get("output") or 0),
            )
            key = _norm(mid)
            if has_toggle:
                toggle_keys.add(key)
            if key not in index or _entry_score(entry) > _entry_score(index[key]):
                index[key] = entry
    for key in toggle_keys:  # 择优条目自己没报 toggle 的才需要回填
        if not index[key].toggle_anywhere:
            index[key] = replace(index[key], toggle_anywhere=True)
    return index


# 模块级缓存：磁盘 JSON 只解析一次；lookup 结果按查询名 memo
_index: dict[str, ModelEntry] | None = None
_lookup_memo: dict[str, Match] = {}

# 用户手动指定的「模型名 → 目录条目 id」，由 provider_store 读盘时发布（见 set_aliases）。
# 按名而非按 profile：「plan-glm-5.2 到底是哪个模型」是名字的属性，不是连接的属性——
# 两个代理都叫这名字，背后几乎必然是同一个模型。而 context / max_tokens 会因端点
# 限流而真的不同，所以那两个仍按 profile 存（provider_store.limits）。
_aliases: dict[str, str] = {}


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


def set_aliases(mapping: dict[str, str]) -> None:
    """发布「模型名 → 目录条目 id」手动映射（全量替换）。

    内容不变则直接返回：这个函数在每次读 lumi.json 的 providers 分区时都会被调用，
    无条件清 memo 等于把 lookup 的缓存废掉。
    """
    global _aliases
    # 入参已由 provider_store._coerce_str_map 清洗（键在 models 内、值非空）；这里
    # 只补索引侧的大小写规则。
    normalized = {_norm(k): _norm(v) for k, v in mapping.items()}
    if normalized != _aliases:
        _aliases = normalized
        _lookup_memo.clear()


def _fuzzy_best(index: dict[str, ModelEntry], query: str) -> ModelEntry | None:
    """相似度最高且过阈值的条目（并列取先见者）；无则 None。"""
    if not index:
        return None
    key = max(index, key=lambda k: fuzz.token_set_ratio(query, k))
    return index[key] if fuzz.token_set_ratio(query, key) >= _MATCH_THRESHOLD else None


def match(model_name: str) -> Match:
    """按模型名查目录：手动指定 → 精确（小写）→ 模糊兜底。

    手动指定优先于精确同名：用户明说了这个别名对应谁，就不该再被同名条目截胡。
    指向的 id 已不在目录里（数据更新后条目消失）时降级为 ``stale``——照旧回落自动
    匹配，但把「你的指定没生效」这件事报出去，而不是让它静默。
    """
    query = _norm(model_name)
    if not query:
        return Match(None, "none")
    # 单次 get 而非「先 in 再取」：set_aliases 会在任意读 providers 分区的线程里清 memo
    # （渠道线程首次 load 即触发），两步之间被清掉会抛 KeyError 进 LLM 调用路径。
    if (cached := _lookup_memo.get(query)) is not None:
        return cached

    index = _get_index()
    pinned = _aliases.get(query, "")
    entry, kind = index.get(pinned), "manual"
    if entry is None:
        entry, kind = index.get(query), "exact"
    if entry is None:
        entry, kind = _fuzzy_best(index, query), "fuzzy"
    if entry is None:
        kind = "none"
    # 指定了却没落到 manual = 目录里查无此条目。entry 仍是自动匹配的结果（运行时用
    # 的就是它），只是把「你的指定没生效」报出去而不让它静默。
    result = Match(entry, "stale" if pinned and kind != "manual" else kind)
    _lookup_memo[query] = result
    return result


def lookup(model_name: str) -> ModelEntry | None:
    """按模型名查目录条目；无缓存/未匹配返回 None。匹配来源见 ``match``。"""
    return match(model_name).entry


def search(query: str, limit: int = 40) -> list[ModelEntry]:
    """目录内按子串搜索（供界面手动指定映射用），短名优先。

    空查询给不出有意义的排序（目录 5000+ 条），返回空表让界面提示先输入。

    整串无果时逐段剥前缀重试（``plan-glm-5.2`` → ``glm-5.2``），取最长的有果后缀：
    界面用模型名预填搜索框，而需要手动指定的恰恰是目录里没有的代理别名——不剥前缀
    的话，最需要帮忙的那一栏永远是空的。这只影响**搜什么**，选谁仍由人决定。
    """
    q = _norm(query)
    if not q:
        return []
    index = _get_index()

    def hits_for(s: str) -> list[ModelEntry]:
        return [e for key, e in index.items() if s in key]

    hits = hits_for(q)
    if not hits:
        # 空后缀（查询以分隔符结尾，用户正打到 "plan-"）跳过：空串是所有 key 的
        # 子串，整个目录会涌进候选列表。
        suffixes = (q[i + 1 :] for i, ch in enumerate(q) if not ch.isalnum())
        hits = next((h for s in suffixes if s and (h := hits_for(s))), [])
    hits.sort(key=lambda e: (len(e.id), e.id))
    return hits[:limit]


def context_window(model_name: str) -> int:
    """模型上下文窗口 token 数；目录未收录 / 无缓存返回 0（= 未知，调用方自行兜底）。

    这是限制取值链的**探测端**：消费方一律走 ``provider_store.limits``（用户覆盖
    优先于本函数），压缩阈值与桌面上下文环由它保证前后端同一口径。
    """
    entry = lookup(model_name)
    return entry.context_length if entry else 0


def max_output_tokens(model_name: str) -> int:
    """模型单次输出 token 上限；目录未收录 / 未标注返回 0（= 未知，调用方自行兜底）。

    探测到多少就是多少，不做封顶：低于真实上限会白白截断长文档输出。
    """
    entry = lookup(model_name)
    return entry.max_output if entry else 0


async def refresh(force: bool = False) -> None:
    """拉取 models.dev 数据写入磁盘缓存（TTL 内跳过）；失败静默沿用旧缓存。"""
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
    atomic_write_json(path, {"fetched_at": time.time(), "data": raw})
    _invalidate()
    logger.info("[ModelCatalog] models.dev 数据已更新")

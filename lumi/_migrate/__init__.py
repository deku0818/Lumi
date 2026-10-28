"""临时旧数据迁移，仅 lumi migrate 按需导入；未来连同 CLI 入口整包删除。"""

from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from functools import partial
from pathlib import Path

from lumi.utils.atomic_io import atomic_write_json, atomic_write_text
from lumi.utils.hashing import short_hash
from lumi.utils.paths import lumi_home


def _connection(profile: dict, model: str, use_responses_api: bool) -> tuple[str, str]:
    """将旧连接的有效接口和方言固定为显式配置。"""
    name = model.lower()
    protocol = profile.get("protocol")
    if not protocol:
        if any(x in name for x in ("claude", "anthropic", "minimax")):
            protocol = "anthropic"
        else:
            protocol = "responses" if use_responses_api else "openai"
    dialect = profile.get("compatibility")
    if not dialect:
        if protocol == "anthropic":
            dialect = "minimax" if "minimax" in name else "claude"
        elif protocol == "gemini":
            dialect = "gemini"
        elif protocol == "responses":
            dialect = "standard"
        else:
            dialect = next(
                (
                    x
                    for x in ("qwen", "deepseek", "kimi", "mimo", "glm", "minimax")
                    if x in name
                ),
                "local",
            )
    return protocol, dialect


def migrate_config(
    data: dict, *, use_responses_api: bool = False
) -> tuple[dict, str, dict]:
    """转换旧供应商/机器人配置，返回旧机器人 id 和模型连接映射。"""
    data = deepcopy(data)
    providers = data.get("providers", {})
    profiles = []
    model_profiles: dict[tuple[str, str], str] = {}
    for profile in providers.get("profiles", []):
        models = profile.get("models") or (
            [profile["model"]] if profile.get("model") else []
        )
        groups = defaultdict(list)
        for model in models or [""]:
            protocol, dialect = _connection(profile, model, use_responses_api)
            groups[protocol, dialect].append(model)
        for index, ((protocol, dialect), group) in enumerate(groups.items()):
            pid = (
                profile["id"] if index == 0 else f"{profile['id']}-{protocol}-{dialect}"
            )
            current = {
                **profile,
                "id": pid,
                "models": [m for m in group if m],
                "protocol": protocol,
                "compatibility": dialect,
            }
            current.pop("model", None)
            for key in ("effort", "context", "max_tokens", "catalog"):
                if isinstance(current.get(key), dict):
                    current[key] = {m: v for m, v in current[key].items() if m in group}
            profiles.append(current)
            model_profiles.update({(profile["id"], m): pid for m in group})
    if "providers" in data:
        providers["profiles"] = profiles
        providers.pop("effort", None)
        for key in ("active", "classifier", "titler"):
            pointer = providers.get(key)
            if isinstance(pointer, str):
                profile = next((p for p in profiles if p["id"] == pointer), None)
                pointer = {
                    "provider": pointer,
                    "model": profile["models"][0]
                    if profile and profile["models"]
                    else "",
                }
            if isinstance(pointer, dict):
                providers[key] = {
                    **pointer,
                    "provider": model_profiles.get(
                        (pointer.get("provider"), pointer.get("model")),
                        pointer.get("provider", ""),
                    ),
                }
    channels = data.get("channels", {})
    bots = channels.get("feishu", [])
    legacy_bot = ""
    if isinstance(bots, dict):
        legacy_bot = short_hash(bots.get("app_id") or "default")
        bots = [
            {
                **bots,
                "id": legacy_bot,
                "name": bots.get("name") or "飞书机器人",
            }
        ]
    for bot in bots:
        if bot.pop("legacy_threads", False):
            legacy_bot = bot["id"]
    if "feishu" in channels:
        channels["feishu"] = bots
    return data, legacy_bot, model_profiles


async def migrate(*, apply: bool = False) -> list[str]:
    """显式检查/迁移本机数据。无自动备份、无启动钩子。"""
    from lumi._migrate.checkpoints import (
        migrate_checkpoints,
        rename_thread,
        rewrite_references,
    )
    from lumi._migrate.memory import migrate_memory_directory
    from lumi.utils.config.global_manager import GlobalConfigManager

    root = lumi_home()
    from lumi.utils.config import get_config

    backend = get_config(str(root)).config  # 与 serve 一样固定本机配置，不随项目漂移。
    path = root / "lumi.json"
    original = json.loads(path.read_text("utf-8")) if path.exists() else {}
    config, legacy_bot, model_profiles = migrate_config(
        original,
        use_responses_api=bool(
            backend.llm_params.get_params_for_model_type("openai").get(
                "use_responses_api"
            )
        ),
    )
    checkpoint_dir = GlobalConfigManager.load().get_checkpoint_dir()
    rename = partial(
        rename_thread,
        legacy_bot=legacy_bot,
        bot_ids={b["id"] for b in config.get("channels", {}).get("feishu", [])},
    )
    updates: dict[Path, object] = {}
    config_changed = config != original
    for name in ("session_meta.json", "session_forks.json"):
        sidecar = checkpoint_dir / name
        if not sidecar.exists():
            continue
        value = json.loads(sidecar.read_text("utf-8"))
        changed = rewrite_references(value, rename)
        if name == "session_meta.json":
            for meta in changed.values():
                uploads = meta.pop("fork_uploads", [])
                if uploads:
                    meta["shared_uploads"] = list(
                        dict.fromkeys([*meta.get("shared_uploads", []), *uploads])
                    )
                pair = meta.get("model_provider"), meta.get("model")
                if pair in model_profiles:
                    meta["model_provider"] = model_profiles[pair]
        if changed != value:
            updates[sidecar] = changed
    memory = {}
    for entry in (root / "memory" / "projects").glob("*/MEMORY.md"):
        memory.update(migrate_memory_directory(entry.parent))
    count = await migrate_checkpoints(
        checkpoint_dir, rename=rename, apply=apply, model_profiles=model_profiles
    )
    if apply:
        for target, value in updates.items():
            atomic_write_json(target, value, mode=0o600)
        for target, content in memory.items():
            atomic_write_text(target, content)
        # 最后清除旧配置中的迁移依据；此前任何一步失败都能按原映射安全重跑。
        if config_changed:
            atomic_write_json(path, config, mode=0o600)
    mode = "已迁移" if apply else "待迁移"
    return [
        f"{mode}：{len(updates) + int(config_changed)} 个配置/元数据文件，{len(memory)} 个记忆文件，{count} 条数据库记录。",
        "前端旧字体偏好回到默认值；新建飞书机器人请显式使用 --new。",
        "检查完成；执行 lumi migrate --apply 应用迁移。"
        if not apply
        else "迁移完成，未创建备份。",
    ]

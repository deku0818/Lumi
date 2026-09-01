"""IM channel 配置持久化 —— ``~/.lumi/lumi.json`` 的 "channels" 分区（含密钥，chmod 600）。

由 desktop UI 经 WS RPC 读写（``get_channels`` / ``save_channel`` / ``delete_channel``），
与 config.json 解耦。经 ``user_store`` section-patch 原子写；缺失/损坏返回默认。

    {"feishu": [{"id": str, "name": str, "enabled": bool, "app_id": str,
                 "app_secret": str, "allow_from": [str], "group_policy": "mention|open",
                 "tool_mode": "auto|privileged", "workspace": str, ...}, ...]}

一台机器多个机器人（列表一条一个），项目 ↔ 机器人 1:1、app_id 全局唯一（同一飞书
应用起两条长连接会重复收事件）。旧版单对象格式在读取时就地迁移成单元素列表。
"""

from __future__ import annotations

import functools
import os
import uuid
from pathlib import Path

from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.utils.config import user_store
from lumi.utils.hashing import short_hash
from lumi.utils.logger import logger


def _read() -> dict:
    """读取 lumi.json 的 "channels" 分区一次；缺失/损坏返回空 dict。"""
    return user_store.read_section("channels", {})


def config_path() -> str:
    """配置（含密钥）落盘的绝对路径，供面板原样展示。

    「存在哪」这件事归本模块管：调用方各自去问 user_store 的话，日后换存储位置
    就得同时改好几处，而面板显示的路径与真实写入位置分家是最不该发生的一种谎。
    """
    return str(user_store.CONFIG_FILE)


def _raw_bots(data: dict) -> list[dict]:
    """channels 分区里的飞书机器人原始条目列表；旧版单对象就地迁移（仅内存，不落盘）。

    迁移 id 取 app_id 摘要而非随机数，故各进程各次读取都迁出同一结果——读路径保持
    纯读（shell spawn 等热路径也会走到这里，从读代码里写盘既堵事件循环又与并发写
    互撞），落盘交给首次 save/delete 顺带完成。``legacy_threads=True`` 让这条机器人
    的会话沿用旧 ``feishu-{key}``，历史不丢。
    """
    raw = data.get("feishu")
    if isinstance(raw, dict):
        migrated = {
            **raw,
            "id": short_hash(raw.get("app_id") or "default"),
            "name": raw.get("name") or "飞书机器人",
            "legacy_threads": True,
        }
        data["feishu"] = [migrated]
        return data["feishu"]
    return raw if isinstance(raw, list) else []


def load_feishu_bots() -> list[FeishuChannelConfig]:
    """读取全部飞书机器人；非法条目跳过（只读路径不回写，凭证不丢）。"""
    bots: list[FeishuChannelConfig] = []
    for entry in _raw_bots(_read()):
        try:
            bots.append(FeishuChannelConfig.model_validate(entry))
        except Exception:
            logger.warning("channels 分区飞书机器人条目校验失败，跳过", exc_info=True)
    return bots


def same_app(a: str, b: str) -> bool:
    """两个 app_id 是否指向同一应用（展开 ${ENV} 引用后比较；空值不算撞）。

    公开供 ``lark_profile.save_bot_synced`` 判「app 未变可跳过同步」——判据只此一份。
    """
    ea, eb = os.path.expandvars(a), os.path.expandvars(b)
    return bool(ea) and ea == eb


def validate_feishu_bot(config: dict) -> FeishuChannelConfig:
    """校验一个机器人配置（不落盘），返回规范化后的配置（id 为空则分配）。

    - 启用态必须绑定项目（无 cwd 兜底）。校验只卡 enabled——否则老配置（历史遗留的
      无项目启用态）连「关掉它」这一步都保存不了
    - 项目 ↔ 机器人 1:1：同一项目不能绑两个机器人（会话按项目路由到唯一身份）
    - app_id 全局唯一：同一飞书应用起两条 WS 长连接会重复收事件

    这些规则刻意不放进 pydantic 模型：``load_feishu_bots`` 吞 ValidationError 跳过
    条目，规则搬进模型会让老记录在 UI 上静默消失，且 RPC 只能吐一段 pydantic 报文
    而非这句人话。占用检查只算校验得过的条目——非法条目挂不起 channel，让它占着
    项目/app 会锁死用户（不可见又删不掉）。

    独立于 :func:`save_feishu_bot` 暴露：``save_bot_synced`` 要在跑 lark-cli 同步
    **之前**把校验错误挡下来，免得为一条存不进去的配置先建出个孤儿 profile。
    """
    validated = FeishuChannelConfig.model_validate(config)
    if not validated.id:
        validated = validated.model_copy(update={"id": uuid.uuid4().hex[:8]})
    if validated.enabled and not validated.workspace:
        raise ValueError(
            "启用飞书机器人前必须绑定项目（设置 → 渠道 → 该机器人 → 绑定项目）"
        )
    for b in _raw_bots(_read()):
        if b.get("id") == validated.id:
            continue
        try:
            FeishuChannelConfig.model_validate(b)
        except Exception:
            continue
        if validated.workspace and b.get("workspace") == validated.workspace:
            raise ValueError(
                f"该项目已被机器人「{b.get('name') or b.get('id')}」绑定，一个项目只能配一个机器人"
            )
        if same_app(b.get("app_id") or "", validated.app_id):
            raise ValueError(
                f"App ID 已被机器人「{b.get('name') or b.get('id')}」使用，同一应用不能配两条"
            )
    return validated


def save_feishu_bot(config: dict) -> FeishuChannelConfig:
    """校验并持久化一个机器人（upsert by id），返回规范化后的配置。"""
    return persist_feishu_bot(validate_feishu_bot(config))


def persist_feishu_bot(validated: FeishuChannelConfig) -> FeishuChannelConfig:
    """落盘一个**已校验**的机器人（upsert by id）。

    与校验分开暴露：``save_bot_synced`` 的校验与落盘之间隔着 lark-cli 子进程，
    合在一起就得把全部校验规则跑两遍。
    """
    data = _read()
    entries = _raw_bots(data)
    for i, b in enumerate(entries):
        if b.get("id") == validated.id:
            entries[i] = validated.model_dump()
            break
    else:
        entries.append(validated.model_dump())
    data["feishu"] = entries
    user_store.write_section("channels", data)
    return validated


def delete_feishu_bot(bot_id: str) -> dict | None:
    """删除一个机器人，返回被删原始条目（供调用方回收 lark-cli profile）；不存在返回 None。

    返回 raw dict 而非 validated 配置：校验不过的坏条目也必须能删干净，且回收
    profile 只需要 id / cli_profile 两个字段，坏条目里它们通常还在。
    """
    data = _read()
    entries = _raw_bots(data)
    removed = next((b for b in entries if b.get("id") == bot_id), None)
    if removed is None:
        return None
    data["feishu"] = [b for b in entries if b.get("id") != bot_id]
    user_store.write_section("channels", data)
    return removed


@functools.lru_cache(maxsize=1)
def _workspace_profiles(path: str, mtime_ns: int) -> tuple[tuple[Path, str], ...]:
    """(项目根, cli_profile) 查表，按 (配置文件路径, mtime) 缓存——shell / 后台任务
    每次 spawn 都要查，不该次次读盘 + 全量 pydantic 校验。键含路径防测试等场景
    换文件后 mtime 撞值。深路径在前：嵌套项目取最近的机器人。"""
    pairs = [
        (Path(b.workspace).resolve(), b.cli_profile)
        for b in load_feishu_bots()
        if b.cli_profile and b.workspace
    ]
    pairs.sort(key=lambda p: len(p[0].parts), reverse=True)
    return tuple(pairs)


def shell_env_for(working_dir: str) -> dict[str, str]:
    """会话工作目录 → 该项目专属机器人的 lark-cli 环境变量（无绑定返回空）。

    serve 启动时注册为 shell env provider（见 ``manager.channels_runtime``）：项目绑了
    机器人且 profile 已同步（``cli_profile`` 非空），该项目所有会话（飞书渠道 + desktop
    同规则）经 Bash 调 lark-cli 就自动是这个机器人的身份。**项目子目录同样命中**——
    后台任务以 shell 当前 cwd spawn，cd 进子目录不该丢身份。没绑或未同步不注入，
    回落用户自己的全局 active profile。profile 事后丢失时 lark-cli 硬报错并指出
    LARKSUITE_CLI_PROFILE 来源，不会静默串到别的身份。
    """
    if not working_dir:
        return {}
    try:
        mtime = user_store.CONFIG_FILE.stat().st_mtime_ns
    except OSError:
        return {}
    target = Path(working_dir).resolve()
    for workspace, profile in _workspace_profiles(str(user_store.CONFIG_FILE), mtime):
        if target == workspace or target.is_relative_to(workspace):
            return {"LARKSUITE_CLI_PROFILE": profile}
    return {}

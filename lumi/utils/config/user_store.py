"""``~/.lumi/lumi.json`` 单文件多分区共享存储。

四个用户级领域各占一个顶级 key，共用一次读盘 / section-patch 原子写：

    {"settings":  {...},   # 全局终端设置
     "projects":  [...],   # 工作目录清单
     "providers": {...},   # 模型连接 + 密钥
     "channels":  {...}}   # IM channel 配置 + 密钥

含密钥，整体 chmod 600。各领域模块（global_manager / projects / provider_store /
channels.store）保留自身 API，内部委托本模块读写自己的分区——分区间互不干扰，
写入只 patch 自己那一段。旧的分散配置文件由 ``scripts/migrate_config.py`` 一次性合并。
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.utils.atomic_io import atomic_write_json
from lumi.utils.logger import logger

CONFIG_FILE: Path = Path.home() / ".lumi" / "lumi.json"


def _read_all() -> dict:
    """读取整份 lumi.json 一次；缺失/损坏返回空 dict。

    ValueError 覆盖 json.JSONDecodeError 与 read_text 的 UnicodeDecodeError（均为其子类），
    OSError 覆盖读盘失败——任一都回落空 dict，不让损坏文件崩掉全部分区读取。
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text("utf-8"))
    except (ValueError, OSError):
        logger.warning("配置读取失败: %s", CONFIG_FILE, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def read_section(key: str, default):
    """读取某分区；缺失、或值类型与 default 不符（文件损坏）时返回 default。

    类型兜底集中在此：调用方传具体类型的 default（dict/list），拿到损坏成异类型的分区值
    时统一回落，省去各调用方各自 isinstance 复核。default 为 None 时不做类型约束。
    """
    val = _read_all().get(key, default)
    if default is not None and not isinstance(val, type(default)):
        return default
    return val


def write_section(key: str, value) -> None:
    """section-patch 原子写：读全量 → 替换该分区 → 整体写回（chmod 600）。"""
    data = _read_all()
    data[key] = value
    atomic_write_json(CONFIG_FILE, data, mode=0o600)  # atomic_write 内部已建父目录

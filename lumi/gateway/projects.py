"""项目清单：手动登记的工作目录列表（~/.lumi/projects.json）。

纯手动语义：只有显式添加过的目录才出现在列表里；移除只删条目、不动磁盘。
切换工作目录时经 touch_project 更新 last_used，列表按最近使用降序。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from lumi.utils.atomic_io import atomic_write_json
from lumi.utils.logger import logger

_PROJECTS_FILE = Path.home() / ".lumi" / "projects.json"


def _by_recent(projects: list[dict]) -> list[dict]:
    return sorted(projects, key=lambda p: p.get("last_used", 0), reverse=True)


def _load() -> list[dict]:
    if not _PROJECTS_FILE.exists():
        return []
    try:
        return json.loads(_PROJECTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("项目清单读取失败: %s", _PROJECTS_FILE, exc_info=True)
        return []


def _save(projects: list[dict]) -> list[dict]:
    """按最近使用排序后原子写盘，返回排序结果（落盘与返回值一致）。"""
    ordered = _by_recent(projects)
    atomic_write_json(_PROJECTS_FILE, ordered)
    return ordered


def list_projects() -> list[dict]:
    """按最近使用降序返回项目列表。"""
    return _by_recent(_load())


def add_project(path: str, name: str = "") -> list[dict]:
    """登记项目（按 path 去重），返回最新列表。

    name 缺省用目录末端名；重复添加刷新 last_used，仅显式给名时才覆盖旧名
    （保护用户此前的重命名）。
    """
    target = Path(path).expanduser().resolve()
    if not target.is_dir():
        raise ValueError(f"目录不存在: {target}")
    resolved = str(target)
    projects = _load()
    for p in projects:
        if p["path"] == resolved:
            p["last_used"] = time.time()
            if name.strip():
                p["name"] = name.strip()
            return _save(projects)
    projects.append(
        {
            "name": name.strip() or target.name,
            "path": resolved,
            "last_used": time.time(),
        }
    )
    return _save(projects)


def remove_project(path: str) -> list[dict]:
    """从清单移除项目（不动磁盘），返回最新列表。"""
    return _save([p for p in _load() if p["path"] != path])


def rename_project(path: str, name: str) -> list[dict]:
    """重命名项目，返回最新列表。"""
    projects = _load()
    for p in projects:
        if p["path"] == path and name.strip():
            p["name"] = name.strip()
    return _save(projects)


def touch_project(path: str) -> None:
    """刷新项目的最近使用时间（未登记的路径忽略）。"""
    projects = _load()
    for p in projects:
        if p["path"] == path:
            p["last_used"] = time.time()
            _save(projects)
            return

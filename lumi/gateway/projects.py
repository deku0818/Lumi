"""项目清单：手动登记的工作目录列表（~/.lumi/lumi.json 的 "projects" 分区）。

纯手动语义：只有显式添加过的目录才出现在列表里；移除只删条目、不动磁盘。
切换工作目录时经 touch_project 更新 last_used，列表按最近使用降序。
"""

from __future__ import annotations

import time
from pathlib import Path

from lumi.utils.config import user_store


def _resolve(path: str) -> str:
    """规范化为与 add_project 落盘一致的形态（展开 ~、绝对化、解析软链）。"""
    return str(Path(path).expanduser().resolve())


def _by_recent(projects: list[dict]) -> list[dict]:
    return sorted(projects, key=lambda p: p.get("last_used", 0), reverse=True)


def _load() -> list[dict]:
    return user_store.read_section("projects", [])


def _save(projects: list[dict]) -> list[dict]:
    """按最近使用排序后原子写盘，返回排序结果（落盘与返回值一致）。"""
    ordered = _by_recent(projects)
    user_store.write_section("projects", ordered)
    return ordered


def list_projects() -> list[dict]:
    """按最近使用降序返回项目列表。

    顺带一次性回填默认项目：默认项目机制上线前登记的条目不含 default 键，这批用户
    每次新建会话都要过一趟项目选择器。清单非空且无任何条目带该键（= 从没碰过这个
    机制）时，把最近使用的标为默认；主动取消过的人条目上是 `default: False`——带键
    即不回填，意愿不会被覆盖。
    """
    projects = _by_recent(_load())
    if projects and not any("default" in p for p in projects):
        return set_default_project(projects[0]["path"], True)
    return projects


def add_project(path: str, name: str = "") -> list[dict]:
    """登记项目（按 path 去重），返回最新列表。

    name 缺省用目录末端名；重复添加刷新 last_used，仅显式给名时才覆盖旧名
    （保护用户此前的重命名）。清单原本为空时，首个项目自动成为默认项目。

    非首个项目**不写** default 键：该键的语义是「此条目对默认表过态」，也是
    ``list_projects`` 回填的哨兵——若这里写个 `default: False`，老用户（条目全无该键）
    只要先加一个项目就把哨兵熄灭，从此再也等不到回填。
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
    entry = {
        "name": name.strip() or target.name,
        "path": resolved,
        "last_used": time.time(),
    }
    # 首个项目即默认：建完就能直接开聊，不必再去卡片菜单里指定一次
    if not projects:
        entry["default"] = True
    projects.append(entry)
    return _save(projects)


def remove_project(path: str) -> list[dict]:
    """从清单移除项目（不动磁盘），返回最新列表。

    删掉的若正是默认项目，把剩下最近使用的顶上来：否则「一个默认都没有」会让每次
    新建会话都退回阻塞式项目选择器，而这个状态自愈不了——剩余条目都带着 default 键，
    ``list_projects`` 的回填哨兵不认。
    """
    target = _resolve(path)
    projects = _load()
    was_default = any(p["path"] == target and p.get("default") for p in projects)
    rest = _by_recent([p for p in projects if p["path"] != target])
    if was_default and rest:
        rest[0]["default"] = True
    return _save(rest)


def rename_project(path: str, name: str) -> list[dict]:
    """重命名项目，返回最新列表。"""
    target = _resolve(path)
    projects = _load()
    for p in projects:
        if p["path"] == target and name.strip():
            p["name"] = name.strip()
    return _save(projects)


def set_default_project(path: str, default: bool) -> list[dict]:
    """设为/取消默认项目（「新建会话」直接落地的项目）。至多一个默认，设新的自动顶掉旧的。

    取消默认（default=False）只清目标自身，不碰其它条目——否则一次针对陈旧/无关
    路径的取消调用会把真正的默认项目也一并清空（多窗口/多端并发操作时可复现）。
    """
    target = _resolve(path)
    projects = _load()
    if default and not any(p["path"] == target for p in projects):
        raise ValueError(f"项目不存在: {target}")
    for p in projects:
        if p["path"] == target:
            p["default"] = default
        elif default:
            p["default"] = False
    return _save(projects)


def touch_project(path: str) -> None:
    """刷新项目的最近使用时间（未登记的路径忽略）。"""
    target = _resolve(path)
    projects = _load()
    for p in projects:
        if p["path"] == target:
            p["last_used"] = time.time()
            _save(projects)
            return

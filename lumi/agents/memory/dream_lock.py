"""Dream 触发的 per-project 锁 + 上次时间 + 进程内并发标记。

锁文件 ``.dream-lock`` 落在该 project 的记忆目录下（与记忆同构、per-project 隔离）：
其 ``mtime`` 即「上次 dream 时间」(lastAt)，时间门据此判断。进程内 ``_in_flight`` 防并发
+ 防自递归二重保险。锁**必须** per-project——全局锁会让一个项目跑完 dream 后、用更新的
lastAt 挡死其他项目的时间门。
"""

from __future__ import annotations

import time
from pathlib import Path

from lumi.agents.memory.paths import memory_dir

LOCK_NAME = ".dream-lock"

_in_flight: set[str] = set()
"""进程内正在跑 dream 的 project key（项目路径串）。"""

_last_scan: dict[str, float] = {}
"""每 project 上次会话扫描时刻——时间门长期满足（会话门老不够）时节流，避免每次 stop 都查 DB。"""


def _lock_path(project_dir: Path) -> Path:
    return memory_dir(project_dir) / LOCK_NAME


def read_last_at(project_dir: Path) -> float:
    """上次 dream 的时间戳（锁文件 mtime，秒）；无锁返 0。"""
    try:
        return _lock_path(project_dir).stat().st_mtime
    except OSError:
        return 0.0


def touch_lock(project_dir: Path) -> None:
    """落锁：写 / 更新 ``.dream-lock``，mtime=now（即下次的 lastAt）。失败不回滚。"""
    path = _lock_path(project_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:
        pass


def throttle_scan(project_dir: Path, min_interval: float) -> bool:
    """距上次会话扫描 < ``min_interval`` 秒返 True（应跳过）；否则记录 now 并返 False。"""
    key = str(project_dir)
    now = time.time()
    if now - _last_scan.get(key, 0.0) < min_interval:
        return True
    _last_scan[key] = now
    return False


def is_in_flight(project_dir: Path) -> bool:
    return str(project_dir) in _in_flight


def mark_in_flight(project_dir: Path) -> None:
    _in_flight.add(str(project_dir))


def clear_in_flight(project_dir: Path) -> None:
    _in_flight.discard(str(project_dir))

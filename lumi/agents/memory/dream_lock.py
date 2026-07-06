"""Dream 触发的持久状态（sqlite）+ 进程内并发/节流。

**持久状态**存独立 sqlite ``~/.lumi/checkpoints/dream_state.db``（与 checkpoints 同区、
用户不碰；不放记忆目录避免清理 ``.md`` 时误删；原子写；时间戳是显式列而非文件 mtime）：

- ``dream_meta(project_key, last_at)``：desktop 短会话上次 dream 的**快照时刻**，
  时间门与「活跃会话」筛选据此。
- ``dream_thread(project_key, thread_id, dreamed_at)``：IM 长会话上次 dream 的快照时刻，
  判活据此（存在落库 ts 晚于它的真实 human 即有新内容）。基于时间戳而非消息计数——
  compact 增删消息不影响判定。

**进程内临时态**（不持久——重启不该还占着）：

- ``project_lock``：per-project ``asyncio.Lock``，dream 综合的**正确性互斥**——所有 dream
  都经 ``_run_dream_fork`` 持锁跑，同一份 MEMORY.md 恒只有一个写者，任何入口都绕不开。
- ``_in_flight``：入口层的**同步快返标记**（fire-and-forget 的 create_task 与任务拿到锁
  之间有空窗，连发两次 /dream 需要它即时挡住）；防自递归二重保险。
- ``_last_scan``：per-project 会话扫描节流。

固定 sqlite（本地小元数据，不跟 checkpoint 的 postgres 后端）、同步 ``sqlite3``（几个
标量、微秒级读写，在 async dream task 里阻塞可忽略）。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from lumi.utils.config import GlobalConfigManager

_in_flight: set[str] = set()
"""进程内已受理（可能尚未拿到锁）的 dream 的 project key（项目路径串）。"""

_project_locks: dict[str, asyncio.Lock] = {}
"""per-project dream 互斥锁——综合写 MEMORY.md 的唯一正确性屏障。"""

_last_scan: dict[str, float] = {}
"""每 project 上次会话扫描时刻——时间门长期满足时节流，避免每次 stop 都查 DB。"""

_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    """懒建 dream_state.db 连接并确保表存在（进程内单连接，dream per-project 串行）。"""
    global _conn
    if _conn is None:
        path = GlobalConfigManager.load().get_checkpoint_dir() / "dream_state.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path))
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS dream_meta"
            "(project_key TEXT PRIMARY KEY, last_at REAL)"
        )
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS dream_thread"
            "(project_key TEXT, thread_id TEXT, dreamed_at REAL,"
            " PRIMARY KEY(project_key, thread_id))"
        )
        # 旧的 per-会话 human 计数游标表已被时间戳判定取代
        _conn.execute("DROP TABLE IF EXISTS dream_cursor")
        _conn.commit()
    return _conn


def read_last_at(project_dir: Path) -> float:
    """desktop 上次 dream 的快照时刻；无记录返 0.0。"""
    row = (
        _db()
        .execute(
            "SELECT last_at FROM dream_meta WHERE project_key=?", (str(project_dir),)
        )
        .fetchone()
    )
    return row[0] if row else 0.0


def record_dream(project_dir: Path, snapshot_ts: float) -> None:
    """desktop dream 成功后写入本次的**快照时刻**（而非完成时刻）。

    dream 后台跑的几分钟里用户新说的话不在快照内，记快照时刻才不会把它们误判为
    「已综合」；下次门控以此为界只看之后的活动。
    """
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO dream_meta(project_key, last_at) VALUES(?, ?)",
        (str(project_dir), snapshot_ts),
    )
    conn.commit()


def read_thread_dreamed_at(project_dir: Path, thread_id: str) -> float:
    """IM 长会话上次 dream 的快照时刻；无记录返 0.0。"""
    row = (
        _db()
        .execute(
            "SELECT dreamed_at FROM dream_thread WHERE project_key=? AND thread_id=?",
            (str(project_dir), thread_id),
        )
        .fetchone()
    )
    return row[0] if row else 0.0


def record_thread_dream(project_dir: Path, thread_id: str, snapshot_ts: float) -> None:
    """IM 长会话 dream 成功后写入该 thread 本次的快照时刻（语义同 ``record_dream``）。"""
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO dream_thread(project_key, thread_id, dreamed_at)"
        " VALUES(?, ?, ?)",
        (str(project_dir), thread_id, snapshot_ts),
    )
    conn.commit()


def throttle_scan(project_dir: Path, min_interval: float) -> bool:
    """距上次会话扫描 < ``min_interval`` 秒返 True（应跳过）；否则记录 now 并返 False。"""
    key = str(project_dir)
    now = time.time()
    if now - _last_scan.get(key, 0.0) < min_interval:
        return True
    _last_scan[key] = now
    return False


def project_lock(project_dir: Path) -> asyncio.Lock:
    """该 project 的 dream 互斥锁（懒建）。``_run_dream_fork`` 持它跑完整个综合。"""
    return _project_locks.setdefault(str(project_dir), asyncio.Lock())


def is_in_flight(project_dir: Path) -> bool:
    """该 project 是否已有 dream 受理中或正在跑（入口快返 + 门控用）。"""
    return str(project_dir) in _in_flight or project_lock(project_dir).locked()


def mark_in_flight(project_dir: Path) -> None:
    _in_flight.add(str(project_dir))


def clear_in_flight(project_dir: Path) -> None:
    _in_flight.discard(str(project_dir))

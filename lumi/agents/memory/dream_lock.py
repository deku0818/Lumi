"""Dream 触发的持久状态（sqlite）+ 进程内并发/节流。

**持久状态**存独立 sqlite ``~/.lumi/checkpoints/dream_state.db``（与 checkpoints 同区、
用户不碰；不放记忆目录避免清理 ``.md`` 时误删；原子写；``last_at`` 是显式列而非文件 mtime）：

- ``dream_meta(project_key, last_at)``：上次 dream 时间戳，时间门据此。
- ``dream_cursor(project_key, thread_id, human_count)``：每会话「上次综合时的真实 human 数」
  游标——human 门据此算 delta（只数游标之后的新增，不被旧消息污染）。

**进程内临时态**（不持久——重启不该还 in_flight）：``_in_flight`` per-project 并发锁 +
防自递归二重保险；``_last_scan`` per-project 会话扫描节流。

固定 sqlite（本地小元数据，不跟 checkpoint 的 postgres 后端）、同步 ``sqlite3``（几个整数、
微秒级读写，在 async dream task 里阻塞可忽略）。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from lumi.utils.config import GlobalConfigManager

_in_flight: set[str] = set()
"""进程内正在跑 dream 的 project key（项目路径串）。"""

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
            "CREATE TABLE IF NOT EXISTS dream_cursor"
            "(project_key TEXT, thread_id TEXT, human_count INTEGER,"
            " PRIMARY KEY(project_key, thread_id))"
        )
        _conn.commit()
    return _conn


def read_last_at(project_dir: Path) -> float:
    """上次 dream 的时间戳；无记录返 0.0（语义同旧的无锁文件）。"""
    row = (
        _db()
        .execute(
            "SELECT last_at FROM dream_meta WHERE project_key=?", (str(project_dir),)
        )
        .fetchone()
    )
    return row[0] if row else 0.0


def load_cursors(project_dir: Path) -> dict[str, int]:
    """读该 project 每会话游标 ``{thread_id: 上次综合时的真实 human 数}``。"""
    rows = (
        _db()
        .execute(
            "SELECT thread_id, human_count FROM dream_cursor WHERE project_key=?",
            (str(project_dir),),
        )
        .fetchall()
    )
    return {tid: n for tid, n in rows}


def record_dream(project_dir: Path, cur_human: dict[str, int]) -> None:
    """dream 成功后**一个事务**原子更新：last_at=now + 游标 upsert。

    upsert（``INSERT OR REPLACE``，**不**整体 DELETE）：只动本次参与的会话游标，**保留没参与
    的老会话游标**——否则它们下次有活动时游标已丢、旧消息会被当新增污染回来。last_at 与游标
    同一 ``commit`` → 不会出现「last_at 推进了但游标没更新」的半更新。
    """
    key = str(project_dir)
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO dream_meta(project_key, last_at) VALUES(?, ?)",
        (key, time.time()),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO dream_cursor(project_key, thread_id, human_count)"
        " VALUES(?, ?, ?)",
        [(key, tid, n) for tid, n in cur_human.items()],
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


def is_in_flight(project_dir: Path) -> bool:
    return str(project_dir) in _in_flight


def mark_in_flight(project_dir: Path) -> None:
    _in_flight.add(str(project_dir))


def clear_in_flight(project_dir: Path) -> None:
    _in_flight.discard(str(project_dir))

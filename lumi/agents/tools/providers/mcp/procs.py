"""MCP stdio 子进程树：后代 PID 收集与强杀（跨平台）。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def collect_descendant_pids(parent_pid: int) -> list[int]:
    """递归收集指定进程的所有后代 PID（跨平台）。"""
    try:
        if sys.platform == "win32":
            return _collect_descendant_pids_windows(parent_pid)
        return _collect_descendant_pids_unix(parent_pid)
    except (OSError, subprocess.SubprocessError):
        return []


def _collect_descendant_pids_unix(parent_pid: int) -> list[int]:
    """Unix: 通过 pgrep 递归收集后代 PID"""
    result = subprocess.run(
        ["pgrep", "-P", str(parent_pid)],
        capture_output=True,
        text=True,
        timeout=2,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    pids: list[int] = []
    for line in result.stdout.strip().split("\n"):
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        pids.append(pid)
        pids.extend(collect_descendant_pids(pid))
    return pids


def _parent_to_children(table: str) -> dict[int, list[int]]:
    """``"<ppid> <pid>"`` 逐行文本 → ``{ppid: [pid, ...]}``（非数字行直接丢弃）。"""
    tree: dict[int, list[int]] = {}
    for line in table.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            ppid, pid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        tree.setdefault(ppid, []).append(pid)
    return tree


def _collect_descendant_pids_windows(parent_pid: int) -> list[int]:
    """Windows: 一次取回全进程表，本地建树后广度收集后代。

    原实现逐层 spawn ``wmic``，而 wmic 自 Win11 23H2/24H2 起默认不再安装、25H2 升级时
    移除、2026 年的功能更新彻底删除且不再作为 FoD 提供——在新版 Windows 上等同于没有
    清理逻辑。官方替代是 PowerShell 的 Get-CimInstance；它启动成本高，故只调一次拿全表，
    递归改在本地做（顺带把原来 S 层子进程压成 1 个）。
    """
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            # -Property 只取两列：默认会把每个进程的四十多个属性全 marshal 回来
            "Get-CimInstance Win32_Process -Property ProcessId,ParentProcessId | "
            'ForEach-Object { "$($_.ParentProcessId) $($_.ProcessId)" }',
        ],
        capture_output=True,
        # 这里只有数字和空格，宽松解码即可——text=True 走 locale（简中 cp936），
        # 撞上 PowerShell 的中文报错行会抛
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if result.returncode != 0:
        return []
    tree = _parent_to_children(result.stdout)

    seen = {parent_pid}
    stack = [parent_pid]
    while stack:
        for pid in tree.get(stack.pop(), ()):
            # PID 复用可能让 ppid 关系成环，seen 保证不会绕不出来
            if pid not in seen:
                seen.add(pid)
                stack.append(pid)
    return list(seen - {parent_pid})


def kill_pids(pids: set[int]) -> None:
    """强制终止指定 PID 集合（仅这些，不波及其它）。

    Unix: SIGTERM → 短暂等待 → SIGKILL 兜底。
    Windows: taskkill /F /PID。
    """
    targets = [p for p in pids if p]
    if not targets:
        return

    if sys.platform == "win32":
        for pid in targets:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        return

    # Unix: 先 SIGTERM 再 SIGKILL
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    time.sleep(0.3)

    for pid in targets:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def kill_child_processes() -> None:
    """SIGKILL 兜底：终止当前进程的**全部**后代。仅进程退出路径可用。"""
    kill_pids(set(collect_descendant_pids(os.getpid())))

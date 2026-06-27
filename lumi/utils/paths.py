"""临时输出目录的单一事实源

所有写到系统临时区的运行期产物（后台任务日志、IM 入站文件等）统一落在
``<系统临时区>/lumi/<子目录>`` 下，集中管理、自动清理，不污染 ``~/.lumi`` 与项目目录。

根目录取 ``tempfile.gettempdir()``（尊重 ``$TMPDIR``，按 OS 用户隔离）而非写死
``/tmp``：Linux 上即 ``/tmp/lumi``，macOS 上落在每用户私有的 ``/var/folders/.../lumi``，
避免多用户共享主机时撞到他人创建的、本用户无写权限的 ``/tmp/lumi``，也不把含用户数据的
产物暴露在全局可读的固定路径下。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

LUMI_TMP_ROOT: Path = Path(tempfile.gettempdir()) / "lumi"
"""Lumi 临时输出根目录（系统临时区下的 lumi/，按 OS 用户隔离）。"""


def lumi_tmp_dir(*parts: str) -> Path:
    """返回 ``LUMI_TMP_ROOT/<parts...>`` 临时子目录，确保已存在。"""
    path = LUMI_TMP_ROOT.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path

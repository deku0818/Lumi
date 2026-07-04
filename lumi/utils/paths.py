"""临时输出目录的单一事实源

所有写到系统临时区的运行期产物（后台任务日志、IM 入站文件、dream 导出等）统一落在
每用户私有的 lumi 根目录下，集中管理、自动清理，不污染 ``~/.lumi`` 与项目目录。

- **POSIX（Linux/macOS）**：``/tmp`` 可写时根取 ``/tmp/lumi-<uid>``——路径短、好找、便于
  调试（macOS 上 ``/tmp`` 即 ``/private/tmp``），用 uid 命名 + ``0700`` 权限 + 属主校验恢复
  每用户隔离，避免多用户共享主机上撞到他人预建目录或被窥探；``/tmp`` 只读的受限环境
  回落 ``gettempdir()/lumi``。
- **Windows**：``gettempdir()`` 本就落在每用户私有的 ``AppData\\Local\\Temp``，直接用其下的
  ``lumi/``，无需额外 uid 命名。

项目/会话维度的细分由调用方显式传入子目录（如 dream 按 :func:`project_slug` 分段），因为
项目根按会话绑定、非进程级 cwd，无法在模块级常量里确定。
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from lumi.utils.workspace_id import get_workspace_id


def _user_tmp_root() -> Path:
    """每用户私有的 lumi 临时根目录（仅构造路径，不落盘）。

    POSIX 且 ``/tmp`` 可写 → ``/tmp/lumi-<uid>``（短路径、好调试）；否则回落
    ``gettempdir()/lumi``（尊重 ``$TMPDIR``），覆盖 Windows 及 ``/tmp`` 只读的受限容器/沙箱。
    """
    if os.name == "posix" and os.access("/tmp", os.W_OK):
        return Path("/tmp") / f"lumi-{os.getuid()}"
    return Path(tempfile.gettempdir()) / "lumi"


LUMI_TMP_ROOT: Path = _user_tmp_root()
"""Lumi 临时输出根目录（每用户私有；POSIX 下 ``/tmp/lumi-<uid>``，强制 ``0700`` 且属主自持）。"""


def project_slug(project_dir: str | os.PathLike[str]) -> str:
    """项目根 → 短且稳定的目录名：``<basename>-<路径哈希6位>``。

    如 ``/Users/y-pc/Cocoon/Lumi`` → ``Lumi-1a2b3c``；同名不同路径不会相撞。
    哈希段复用 :func:`~lumi.utils.workspace_id.get_workspace_id`（路径→id 的单一事实源）。
    """
    resolved = Path(project_dir).resolve()
    return f"{resolved.name}-{get_workspace_id(resolved)[:6]}"


def _ensure_root() -> None:
    """确保根目录存在且为本用户私有：竞态安全创建 + 属主校验 + 收紧 0700。

    POSIX 下根路径可预测（``/tmp/lumi-<uid>``），须防预建劫持：属主非本用户即拒用，
    避免把含用户数据的产物写进他人拥有的目录。
    """
    # exist_ok 令并发首建互不报错（mode 在 Windows/已存在时均被忽略）
    LUMI_TMP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "posix":
        return
    # 已存在时 mode 被忽略，故显式校验属主并收紧权限
    info = LUMI_TMP_ROOT.stat()
    if info.st_uid != os.getuid():
        raise PermissionError(
            f"临时根目录 {LUMI_TMP_ROOT} 属主非本用户（疑似预建劫持），拒绝使用"
        )
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(LUMI_TMP_ROOT, 0o700)


def lumi_tmp_dir(*parts: str) -> Path:
    """返回 ``LUMI_TMP_ROOT/<parts...>`` 临时子目录，确保已存在。

    每次调用都保证根目录存在且为本用户私有（POSIX 0700 + 属主校验）。
    """
    _ensure_root()
    path = LUMI_TMP_ROOT.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path

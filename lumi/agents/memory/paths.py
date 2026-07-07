"""按项目隔离的持久记忆目录单一事实源。

记忆落在 ``~/.lumi/memory/projects/<项目路径sanitize>/``，含 ``MEMORY.md`` 索引
+ 各 topic ``.md`` 文件。与 checkpoints 同为 home 级用户数据，跨会话持久，不随
``.lumi`` 配置发现链漂移（记忆是「这个项目的长期上下文」，按项目根稳定 key）。

sanitize 规则与 Claude Code 一致（``/Users/x/Lumi`` → ``-Users-x-Lumi``），
保留可读性便于调试，而非用不可读的 hash。
"""

from __future__ import annotations

from pathlib import Path

MEMORY_ROOT: Path = Path.home() / ".lumi" / "memory" / "projects"
"""所有项目记忆的根目录（home 级，跨会话持久）。"""

ENTRYPOINT_NAME = "MEMORY.md"
"""记忆索引文件名（常驻注入上下文；各条记忆正文另存 topic .md）。"""


def _sanitize(project_dir: Path) -> str:
    """项目绝对路径 → 目录名安全的可读 key。"""
    text = str(project_dir.resolve())
    return text.replace("/", "-").replace("\\", "-").replace(":", "-")


def memory_dir(project_dir: Path) -> Path:
    """返回该项目的记忆目录（不保证存在，用 :func:`ensure_memory_dir`）。"""
    return MEMORY_ROOT / _sanitize(project_dir)


def memory_entrypoint(project_dir: Path) -> Path:
    """返回该项目的 ``MEMORY.md`` 索引路径。"""
    return memory_dir(project_dir) / ENTRYPOINT_NAME


def ensure_memory_dir(project_dir: Path) -> Path:
    """确保该项目的记忆目录存在并返回，使模型可直接 write 而无需先 mkdir。"""
    target = memory_dir(project_dir)
    target.mkdir(parents=True, exist_ok=True)
    return target


def resolve_under_project(path: str | Path, project_dir: Path) -> Path:
    """相对路径基于项目根解析并 resolve——工具写入路径归一化的单一口径
    （is_memory_path 的免审批判定与 context_inject 的自改静默比对共用）。"""
    target = Path(path)
    if not target.is_absolute():
        target = project_dir / target
    return target.resolve()


def is_memory_path(path: str | Path, project_dir: Path) -> bool:
    """判断 ``path`` 是否落在该项目记忆目录内（相对路径基于项目根解析）。"""
    try:
        return resolve_under_project(path, project_dir).is_relative_to(
            memory_dir(project_dir).resolve()
        )
    except (OSError, ValueError):
        return False


def read_text_or_none(path: Path) -> str | None:
    """读文本并 strip；文件缺失/不可读/为空一律返回 None（记忆与项目说明共用）。"""
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeDecodeError):
        return None

"""原子文件写入工具。

跨子系统共用的底层原语：以「写临时文件再 rename」保证写入不会留下半写状态，
供 checkpoint / provider_store / sessions / projects / model_catalog / cron 等共用。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, mode: int | None = None) -> None:
    """原子写入文本文件（先写临时文件再 rename）。

    使用 tempfile + rename 确保写入不会留下半写状态的文件。
    mode 非 None 时在 rename 前应用文件权限（敏感内容如 api_key 用 0o600）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        if mode is not None:
            os.chmod(tmp, mode)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: object, mode: int | None = None) -> None:
    """原子写入 JSON 文件（基于 :func:`atomic_write_text`）。"""
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2), mode)

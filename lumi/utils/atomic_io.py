"""原子文件写入工具。

跨子系统共用的底层原语：以「写临时文件再 rename」保证写入不会留下半写状态，
供 checkpoint / provider_store / sessions / projects / model_catalog / cron 等共用。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from lumi.utils.logger import logger


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


def read_json_object_for_update(path: Path) -> dict:
    """读-改-写之前的读：缺失为空；损坏（非 JSON / 顶层不是对象）时把原文件改名为
    ``<name>.corrupt-<时间>`` 留档后返回空。

    既不拿空 dict 覆盖抹掉原内容（里面可能是全部密钥），也不让一份坏文件卡死后续
    所有写入（非技术用户没法手修 JSON）。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:  # JSONDecodeError / UnicodeDecodeError
        data = None
    if isinstance(data, dict):
        return data
    backup = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    path.replace(backup)
    logger.error("%s 已损坏，原文件已移到 %s，本次写入从空开始", path, backup)
    return {}

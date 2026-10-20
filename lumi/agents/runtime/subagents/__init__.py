"""Lazy, host-local subagent session service shared by tools and read-only APIs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .service import SubagentService

_services: dict[Path, SubagentService] = {}


def get_subagent_service() -> SubagentService:
    from lumi.utils.config import GlobalConfigManager

    from .service import SubagentService

    path = GlobalConfigManager.load().get_checkpoint_dir() / "subagents.db"
    if path not in _services:
        _services[path] = SubagentService(path)
    return _services[path]


def subagent_service_if_started() -> SubagentService | None:
    from lumi.utils.config import GlobalConfigManager

    path = GlobalConfigManager.load().get_checkpoint_dir() / "subagents.db"
    return _services.get(path)


async def close_subagent_services() -> None:
    services = list(_services.values())
    _services.clear()
    for service in services:
        await service.close()

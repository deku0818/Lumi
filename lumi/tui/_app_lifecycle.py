"""LumiApp theme detection helpers."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.tui.app import LumiApp


async def detect_system_theme() -> bool:
    """Detect OS theme preference. Returns True for dark mode."""
    if sys.platform == "darwin":
        return await _detect_macos_theme()
    if sys.platform == "win32":
        return _detect_windows_theme()
    return True


async def _detect_macos_theme() -> bool:
    """macOS dark theme detection via `defaults` command."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "defaults",
            "read",
            "-g",
            "AppleInterfaceStyle",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        return stdout.decode().strip().lower() == "dark"
    except asyncio.TimeoutError:
        logger.debug("[LumiApp] 系统主题检测超时，使用暗色主题")
        return True
    except FileNotFoundError:
        logger.debug("[LumiApp] 'defaults' 命令不可用，使用暗色主题")
        return True
    except OSError:
        logger.warning("[LumiApp] 系统主题检测意外失败，使用暗色主题", exc_info=True)
        return True


def _detect_windows_theme() -> bool:
    """Windows dark theme detection via registry."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except Exception:
        logger.debug("[LumiApp] Windows 注册表主题检测失败，使用暗色主题")
        return True


async def apply_theme_mode(app: LumiApp, mode: str) -> None:
    """Apply theme mode: 'dark', 'light', or 'system'."""
    if mode == "dark":
        app.theme = "lumi-dark"
    elif mode == "light":
        app.theme = "lumi-light"
    else:
        is_dark = await detect_system_theme()
        logger.info("系统主题检测结果: dark=%s", is_dark)
        app.theme = "lumi-dark" if is_dark else "lumi-light"

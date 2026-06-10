"""主题契约测试：锁住 TUI 色板与 desktop index.css 的双份十六进制值一致。

两边的「保持一致」注释没有任何强制力——色板调整只改一侧时，TUI 与 desktop
会静默漂移。本测试把 CSS 变量名映射到 ThemePalette 字段，断言逐色相等。
"""

from __future__ import annotations

import re
from pathlib import Path

from lumi.tui.theme import DARK_PALETTE, LIGHT_PALETTE, ThemePalette

_CSS = (
    Path(__file__).resolve().parents[1] / "desktop" / "src" / "index.css"
).read_text(encoding="utf-8")

# index.css 的 --color-* 变量名 → ThemePalette 字段（scrollbar 无对应 CSS 变量）
_FIELD_BY_VAR = {
    "canvas": "bg_primary",
    "surface": "bg_surface",
    "panel": "bg_input",
    "line": "border_input",
    "separator": "border_separator",
    "ink": "text_primary",
    "muted": "text_muted",
    "accent": "accent",
    "accent-dim": "accent_dim",
    "success": "success",
    "error": "error",
    "info": "info",
}


def _css_colors(selector: str) -> dict[str, str]:
    """提取某个选择器块内的 --color-* 十六进制变量。"""
    block = re.search(rf"{selector} \{{(.*?)\n\}}", _CSS, re.S).group(1)
    return {
        name: value.lower()
        for name, value in re.findall(r"--color-([\w-]+):\s*(#[0-9a-fA-F]+)", block)
    }


def _palette_colors(palette: ThemePalette) -> dict[str, str]:
    return {
        var: getattr(palette, field).lower() for var, field in _FIELD_BY_VAR.items()
    }


def test_dark_palette_matches_desktop_css():
    css = _css_colors(r":root")
    expected = _palette_colors(DARK_PALETTE)
    assert {k: css.get(k) for k in expected} == expected


def test_light_palette_matches_desktop_css():
    css = _css_colors(r":root\.light")
    expected = _palette_colors(LIGHT_PALETTE)
    assert {k: css.get(k) for k in expected} == expected

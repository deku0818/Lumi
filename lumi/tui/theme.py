"""Lumi TUI 主题 - 基于 Textual Theme 系统

通过注册自定义 Theme 对象（lumi-dark / lumi-light），利用 Textual 的 CSS 变量
机制实现亮暗主题切换。Widget CSS 中使用 $variable 引用颜色。

Textual 内置 CSS 变量与 Lumi 语义角色的映射：
  $foreground          → text_primary（主文本色）
  $text-muted          → text_muted（次要/灰色文本）
  $background          → bg_primary（主背景）
  $surface             → bg_surface（卡片/消息背景）
  $panel               → bg_input（输入框背景）
  $border              → border_separator（分隔线/默认边框）
  $border-blurred      → border_input（输入框边框）
  $accent              → accent（品牌强调色）
  $accent-muted        → accent_dim（弱化强调色）
  $error               → error
  $success             → success
  $secondary           → info（信息/链接色）
  $scrollbar           → scrollbar_default
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from textual.theme import Theme

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThemePalette:
    """单套主题的完整配色方案。

    每个字段对应一个语义化颜色角色，值为十六进制颜色字符串
    （如 ``#e0e0e0``）或字面量 ``"transparent"``。
    """

    # 文本
    text_primary: str
    text_muted: str

    # 背景
    bg_primary: str
    bg_surface: str
    bg_input: str

    # 边框
    border_input: str
    border_separator: str

    # 强调色
    accent: str
    accent_dim: str

    # 语义色
    success: str
    error: str
    info: str

    # 滚动条
    scrollbar_default: str


# ── 暗色色板（暖中性炭灰：去除旧版蓝紫冷调，配暖金 accent；与 desktop index.css 一致） ──

DARK_PALETTE = ThemePalette(
    text_primary="#ececea",
    text_muted="#8f8d87",
    bg_primary="#1a1a19",
    bg_surface="#262624",
    bg_input="#201f1d",
    border_input="#383734",
    border_separator="#565450",
    accent="#ffcc00",
    accent_dim="#66645c",
    success="#4CD964",
    error="#D94C4C",
    info="#42a5f5",
    scrollbar_default="#383734",
)

# ── 亮色色板（暖白系；与 desktop index.css 一致） ──

LIGHT_PALETTE = ThemePalette(
    text_primary="#2b2620",
    text_muted="#78726a",
    bg_primary="#f7f4ed",
    bg_surface="#efebe1",
    bg_input="#fdfbf6",
    border_input="#ddd6c8",
    border_separator="#ccc4b2",
    accent="#b8860b",
    accent_dim="#a89a78",
    success="#3BA855",
    error="#B33A3A",
    info="#1565c0",
    scrollbar_default="#ddd6c8",
)


def _build_theme(name: str, palette: ThemePalette, *, dark: bool) -> Theme:
    """根据色板构建 Textual Theme 对象。

    将 Lumi 语义色板映射到 Textual 内置 CSS 变量，通过 ``variables``
    字典覆盖 Textual 自动生成的默认值。
    """
    return Theme(
        name=name,
        primary=palette.accent,
        secondary=palette.info,
        accent=palette.accent,
        foreground=palette.text_primary,
        background=palette.bg_primary,
        surface=palette.bg_surface,
        panel=palette.bg_input,
        error=palette.error,
        success=palette.success,
        warning=palette.accent,
        dark=dark,
        variables={
            # 覆盖 Textual 自动生成的变量
            "text-muted": palette.text_muted,
            "foreground-muted": palette.text_muted,
            "foreground-disabled": palette.border_separator,
            "border": palette.border_separator,
            "border-blurred": palette.border_input,
            "accent-muted": palette.accent_dim,
            "scrollbar": palette.scrollbar_default,
            "scrollbar-hover": palette.accent,
            "scrollbar-active": palette.accent,
        },
    )


LUMI_DARK_THEME = _build_theme("lumi-dark", DARK_PALETTE, dark=True)
LUMI_LIGHT_THEME = _build_theme("lumi-light", LIGHT_PALETTE, dark=False)


def get_color(role: str) -> str:
    """获取当前主题下指定语义角色的颜色值。

    通过 ``active_app`` 上下文变量获取当前运行的 app 实例，根据当前主题名
    选择暗色或亮色色板，再按 *role* 取对应色值。

    用于 Rich markup 中需要具体十六进制色值的场景（Rich 不支持 $variable）。

    Args:
        role: 语义角色名，如 ``"accent"``, ``"text_primary"``, ``"error"`` 等。

    Returns:
        十六进制颜色字符串，如 ``"#ffcc00"``。
    """
    try:
        from textual import active_app

        app = active_app.get()
        is_dark = getattr(app, "theme", "lumi-dark") == "lumi-dark"
    except LookupError:
        is_dark = True
    except Exception:
        logger.warning("get_color: 获取当前 app 失败，回退到暗色主题", exc_info=True)
        is_dark = True

    palette = DARK_PALETTE if is_dark else LIGHT_PALETTE

    color = getattr(palette, role, None)
    if color is None:
        logger.warning("无效的颜色角色 '%s'，回退到 accent", role)
        color = palette.accent

    return color


APP_CSS = """
/* ── Screen ── */
Screen {
    background: ansi_default;
    color: $foreground;
    layers: base overlay;
}

/* ── 全局滚动条美化 ── */
* {
    scrollbar-size-vertical: 1;
    scrollbar-size-horizontal: 1;
    scrollbar-background: transparent;
    scrollbar-background-hover: transparent;
    scrollbar-background-active: transparent;
    scrollbar-corner-color: transparent;
    scrollbar-color: $scrollbar;
    scrollbar-color-hover: $accent;
    scrollbar-color-active: $accent;
}

/* ── Markdown 内联代码 - 覆盖 Textual 默认的 error 配色 ── */
MarkdownBlock {
    &:light > .code_inline {
        background: #e8e1d3;
        color: $accent;
    }
}

/* ── 聊天日志 ── */
#chat-log {
    background: transparent;
    padding: 0 1;
}

"""

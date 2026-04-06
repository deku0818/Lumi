"""TUI 屏幕共享常量和工具函数"""

# 主题模式选项映射：显示文本 → 配置值
THEME_OPTIONS: list[tuple[str, str]] = [
    ("● 暗色 (Dark)", "dark"),
    ("○ 明亮 (Light)", "light"),
    ("◐ 跟随系统 (System)", "system"),
]


def theme_index_of(value: str) -> int:
    """返回 THEME_OPTIONS 中匹配 value 的索引，未找到返回 0。"""
    return next(
        (i for i, (_, v) in enumerate(THEME_OPTIONS) if v == value),
        0,
    )

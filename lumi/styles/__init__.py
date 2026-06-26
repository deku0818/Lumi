"""内置风格包

每个子目录代表一种风格，可含 prompts/、agents/、skills/ 三类子目录（均可选）。
"""

from pathlib import Path

_STYLES_ROOT = Path(__file__).parent
_IGNORE_DIRS = {"__pycache__"}


def _get_style_subdir(name: str, subdir: str) -> Path:
    """获取指定风格的子目录路径。

    Raises:
        ValueError: 风格不存在或子目录缺失
    """
    style_dir = _STYLES_ROOT / name
    if not style_dir.is_dir():
        available = ", ".join(list_styles()) or "(无)"
        raise ValueError(f"风格 '{name}' 不存在。可用风格: {available}")
    target = style_dir / subdir
    if not target.is_dir():
        raise ValueError(f"风格 '{name}' 缺少 {subdir}/ 目录")
    return target


def get_style_prompts_dir(name: str) -> Path:
    """获取指定风格的 prompts 目录路径。"""
    return _get_style_subdir(name, "prompts")


def get_style_agents_dir(name: str) -> Path:
    """获取指定风格的 agents 目录路径。"""
    return _get_style_subdir(name, "agents")


def get_style_skills_dir(name: str) -> Path:
    """获取指定风格的 skills 目录路径。"""
    return _get_style_subdir(name, "skills")


def list_styles() -> list[str]:
    """列出所有可用的内置风格名称。"""
    return sorted(
        d.name
        for d in _STYLES_ROOT.iterdir()
        if d.is_dir() and d.name not in _IGNORE_DIRS and not d.name.startswith("_")
    )

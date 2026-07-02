"""一次性配置迁移脚本（跑一次即可，不常驻运行时代码）。

两件事：
  1) 用户级：把 ~/.lumi/{lumi,projects,providers,channels}.json 合并成单文件
     ~/.lumi/lumi.json 的四分区（settings/projects/providers/channels），删旧文件。
  2) 项目级：把 <项目>/.lumi/config.yaml 转成 <项目>/.lumi/config.json（丢弃 YAML 注释）。

安全：解析失败的旧文件不并入、也不删除（原样保留供手动修复）；已是新格式则跳过。

用法：
    uv run python scripts/migrate_config.py                # 迁移 ~/.lumi 与 ./.lumi
    uv run python scripts/migrate_config.py <项目目录> ...   # 另指定一个/多个项目目录
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from lumi.utils.atomic_io import atomic_write_json
from lumi.utils.config.global_models import GlobalConfig

# 合并文件顶级分区键（判「已是新格式」）；旧独立文件 → 分区名（settings 来自旧扁平 lumi.json 自身）
_SECTIONS = ("settings", "projects", "providers", "channels")
_INDIVIDUAL = (
    ("projects", "projects.json"),
    ("providers", "providers.json"),
    ("channels", "channels.json"),
)


def _read_json(path: Path):
    """读取并解析 JSON；缺失/损坏返回 None。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (ValueError, OSError):
        print(f"  警告：{path} 解析失败，跳过（原文件保留）")
        return None


def migrate_user_store(lumi_dir: Path) -> str | None:
    """合并 lumi_dir 下四文件到 lumi.json；返回操作说明，无需迁移返回 None。"""
    target = lumi_dir / "lumi.json"
    existing = _read_json(target)
    if isinstance(existing, dict) and any(k in existing for k in _SECTIONS):
        return None  # 已是合并格式
    merged: dict = {}
    if (
        isinstance(existing, dict) and existing
    ):  # 旧扁平 lumi.json → settings（顺带抹废弃字段）
        try:
            merged["settings"] = GlobalConfig(**existing).model_dump()
        except (ValueError, TypeError):
            print("  警告：旧 lumi.json 设置字段非法，settings 回落默认")
    migrated: list[str] = []  # 只删成功并入的
    for section, name in _INDIVIDUAL:
        val = _read_json(lumi_dir / name)
        if val is not None:
            merged[section] = val
            migrated.append(name)
    if not merged:
        return None
    atomic_write_json(target, merged, mode=0o600)
    for name in migrated:
        (lumi_dir / name).unlink(missing_ok=True)
    return f"合并 {sorted(merged)} → {target}"


def migrate_project_config(config_dir: Path) -> str | None:
    """把 config_dir/config.yaml 转成 config.json；返回操作说明，无需迁移返回 None。"""
    legacy = config_dir / "config.yaml"
    target = config_dir / "config.json"
    if target.exists() or not legacy.exists():
        return None
    try:
        data = yaml.safe_load(legacy.read_text("utf-8")) or {}
    except (yaml.YAMLError, OSError):
        print(f"  警告：{legacy} 解析失败，跳过（原文件保留）")
        return None
    atomic_write_json(target, data)
    legacy.unlink(missing_ok=True)
    return f"config.yaml → {target}"


def main(argv: list[str]) -> int:
    home_lumi = Path.home() / ".lumi"
    project_dirs = [Path(a).expanduser() / ".lumi" for a in argv] or [
        Path.cwd() / ".lumi"
    ]

    print(f"用户配置 ({home_lumi}):")
    print(f"  {migrate_user_store(home_lumi) or '无需迁移'}")
    for d in project_dirs:
        print(f"项目配置 ({d}):")
        print(f"  {migrate_project_config(d) or '无需迁移'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

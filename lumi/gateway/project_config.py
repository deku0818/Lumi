"""项目主页资源聚合：按项目目录读取/写入提示词、技能、子 Agent 与记忆。

资源分三层，与运行时加载链完全同源（loader.load_skills/load_agents 与
manager.prompt_layers 是层序的单一事实源）：style 内置（builtin，只读）→
进程配置目录（global，本页只读）→ 项目 ``.lumi/``（project，可编辑），
逐层同名覆盖——UI 展示的「生效」即会话实际加载的。

写/删/复制只作用于项目层；其余层资源经「复制到项目」产生可编辑副本后再改。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from lumi.agents.memory.paths import memory_dir
from lumi.agents.tools.loader import (
    _load_agents_from_dir,
    _load_skills_from_dir,
    config_layers,
    validate_definition,
)
from lumi.styles import STYLES_ROOT
from lumi.utils.config.manager import strip_frontmatter
from lumi.utils.read_config import get_config

PROMPT_NAMES = ("SOUL", "AGENTS")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _check_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"非法名称: {name!r}")
    return name


def _display_path(path: Path, project: Path) -> str:
    """按来源显示路径：styles/ 相对、项目内 .lumi/ 相对、进程配置层绝对路径。"""
    if path.is_relative_to(STYLES_ROOT):
        return f"styles/{path.relative_to(STYLES_ROOT)}"
    if path.is_relative_to(project):
        return str(path.relative_to(project))
    return str(path)


# ------------------------------------------------------------------
# 聚合概览
# ------------------------------------------------------------------


def overview(project: Path) -> dict:
    """项目主页一次拉全：prompts / skills / agents / memory。"""
    project = project.resolve()
    return {
        "style": get_config().active_style_for(project),
        "prompts": [_prompt_info(project, n) for n in PROMPT_NAMES],
        "skills": _list_skills(project),
        "agents": _list_agents(project),
        "memory": _list_memory(project),
    }


def _prompt_info(project: Path, name: str) -> dict:
    """命中层解析走 manager.resolve_prompt（与运行时 load_prompt 同一份判定）。"""
    resolved = get_config().resolve_prompt(name, project)
    if resolved is None:
        return {"name": name, "source": "", "path": "", "content": "", "body": ""}
    source, path, content = resolved
    return {
        "name": name,
        "source": source,
        "path": _display_path(path, project),
        "content": content,
        "body": strip_frontmatter(content),
    }


def _list_skills(project: Path) -> list[dict]:
    # 以目录名为身份归并（CRUD 按目录定位；写入时校验 frontmatter name 与目录名一致）
    merged: dict[str, dict] = {}
    for source, layer in config_layers("skills", project):
        for s in _load_skills_from_dir(layer).values():
            dir_name = Path(s.path or "").parent.name
            merged[dir_name] = {
                "name": dir_name,
                "description": s.description,
                "source": source,
                "builtin": source != "project",
            }
    return sorted(merged.values(), key=lambda s: (s["builtin"], s["name"]))


def _list_agents(project: Path) -> list[dict]:
    merged: dict[str, dict] = {}
    for source, layer in config_layers("agents", project):
        for a in _load_agents_from_dir(layer).values():
            stem = Path(a.path or "").stem
            merged[stem] = {
                "name": stem,
                "description": a.description,
                "tools": a.tools,
                "source": source,
                "builtin": source != "project",
            }
    return sorted(merged.values(), key=lambda a: (a["builtin"], a["name"]))


def _list_memory(project: Path) -> list[dict]:
    root = memory_dir(project)
    if not root.is_dir():
        return []
    files = sorted(
        (f for f in root.glob("*.md") if f.is_file()),
        key=lambda f: (f.name != "MEMORY.md", f.name),  # MEMORY.md 索引置顶
    )
    return [{"name": f.name, "size": f.stat().st_size} for f in files]


# ------------------------------------------------------------------
# 单资源定位与读写
# ------------------------------------------------------------------


def _skill_dir(project: Path, name: str) -> tuple[Path, str]:
    """定位技能目录，返回 (目录, 来源标签)。高优先层先命中。"""
    _check_name(name)
    for source, layer in reversed(config_layers("skills", project)):
        candidate = layer / name
        if (candidate / "SKILL.md").is_file():
            return candidate, source
    raise ValueError(f"技能不存在: {name}")


def _agent_file(project: Path, name: str) -> tuple[Path, str]:
    """定位 agent 定义文件，返回 (文件, 来源标签)。高优先层先命中。"""
    _check_name(name)
    for source, layer in reversed(config_layers("agents", project)):
        candidate = layer / f"{name}.md"
        if candidate.is_file():
            return candidate, source
    raise ValueError(f"Agent 不存在: {name}")


def _skill_file_in(skill_dir: Path, file: str) -> Path:
    """校验并解析技能内的相对文件路径（防 .. 越界）。"""
    target = (skill_dir / file).resolve()
    if not target.is_relative_to(skill_dir.resolve()):
        raise ValueError(f"非法文件路径: {file!r}")
    return target


def read_resource(project: Path, kind: str, name: str, file: str = "") -> dict:
    """读单个资源全文。skill 额外返回目录内文件清单。

    ``body`` 恒为剥掉 frontmatter 的正文——阅读视图用它，编辑视图用原文 ``content``。
    剥离只在后端做（parse_frontmatter 单一事实源），前端不再自备解析。
    """
    project = project.resolve()
    if kind == "skill":
        skill_dir, source = _skill_dir(project, name)
        files = sorted(
            (
                str(f.relative_to(skill_dir))
                for f in skill_dir.rglob("*.md")
                if f.is_file()
            ),
            key=lambda f: (f != "SKILL.md", f),  # SKILL.md 置顶
        )
        target = _skill_file_in(skill_dir, file or "SKILL.md")
        content = target.read_text("utf-8")
        return {
            "content": content,
            "body": strip_frontmatter(content),
            "files": files,
            "source": source,
            "builtin": source != "project",
            "path": _display_path(skill_dir, project),
        }
    if kind == "agent":
        agent_file, source = _agent_file(project, name)
        content = agent_file.read_text("utf-8")
        return {
            "content": content,
            "body": strip_frontmatter(content),
            "source": source,
            "builtin": source != "project",
            "path": _display_path(agent_file, project),
        }
    if kind == "prompt":
        if name not in PROMPT_NAMES:
            raise ValueError(f"未知提示词: {name}")
        return _prompt_info(project, name)
    if kind == "memory":
        _check_name(name)
        content = (memory_dir(project) / name).read_text("utf-8")
        return {"content": content, "body": strip_frontmatter(content)}
    raise ValueError(f"未知资源类型: {kind}")


def write_resource(
    project: Path, kind: str, name: str, content: str, file: str = ""
) -> dict:
    """写项目层资源（新建或覆盖），按需建目录。其余层不可写。"""
    project = project.resolve()
    if kind == "skill":
        _check_name(name)
        if not file or file == "SKILL.md":
            validate_definition(content, name)
        skill_dir = project / ".lumi" / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        target = _skill_file_in(skill_dir, file or "SKILL.md")
    elif kind == "agent":
        _check_name(name)
        validate_definition(content, name)
        target = project / ".lumi" / "agents" / f"{name}.md"
    elif kind == "prompt":
        if name not in PROMPT_NAMES:
            raise ValueError(f"未知提示词: {name}")
        target = project / ".lumi" / "prompts" / f"{name}.md"
    else:
        raise ValueError(f"资源类型不可写: {kind}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"ok": True, "path": _display_path(target, project)}


def delete_resource(project: Path, kind: str, name: str) -> dict:
    """删项目层资源。同名低层（global/style）仍在时相当于「恢复该层版本」。"""
    project = project.resolve()
    _check_name(name)
    if kind == "skill":
        target = project / ".lumi" / "skills" / name
        if not (target / "SKILL.md").is_file():
            raise ValueError(f"项目内无此技能: {name}（非项目层不可删除）")
        shutil.rmtree(target)
    elif kind == "agent":
        target = project / ".lumi" / "agents" / f"{name}.md"
        if not target.is_file():
            raise ValueError(f"项目内无此 Agent: {name}（非项目层不可删除）")
        target.unlink()
    else:
        raise ValueError(f"资源类型不可删: {kind}")
    # 删除后还能定位到 = 低层同名仍在，效果为恢复该层版本
    try:
        (_skill_dir if kind == "skill" else _agent_file)(project, name)
        restored = True
    except ValueError:
        restored = False
    return {"ok": True, "restored_builtin": restored}


def copy_builtin(project: Path, kind: str, name: str) -> dict:
    """把内置/全局层资源复制到项目层产生可编辑副本（同名覆盖机制随即生效）。"""
    project = project.resolve()
    if kind == "skill":
        src, source = _skill_dir(project, name)
        if source == "project":
            raise ValueError(f"技能已在项目内: {name}")
        dst = project / ".lumi" / "skills" / name
        # dirs_exist_ok：项目层可能残留无 SKILL.md 的同名目录（此时仍判低层），
        # 复制应补全而非撞 FileExistsError
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return {"ok": True, "path": _display_path(dst, project)}
    if kind == "agent":
        src, source = _agent_file(project, name)
        if source == "project":
            raise ValueError(f"Agent 已在项目内: {name}")
        dst = project / ".lumi" / "agents" / f"{name}.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return {"ok": True, "path": _display_path(dst, project)}
    raise ValueError(f"资源类型不可复制: {kind}")

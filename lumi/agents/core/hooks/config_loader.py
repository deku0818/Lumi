"""把三级 ``hooks.json`` 加载为已注册的 Shell hook。

配置文件（优先级从低到高，与 permissions.json 同级同模式，支持 JSONC）：
1. 用户全局 ``~/.lumi/hooks.json``
2. 项目共享 ``{project}/.lumi/hooks.json``
3. 项目本地 ``{project}/.lumi/hooks.local.json``

格式（顶层 event → spec 数组）：
::

    {
      "PreToolUse": [
        {"command": "/abs/path/audit.sh", "matcher": "bash", "timeout": 5000}
      ],
      "Stop": [
        {"command": "/abs/path/on_stop.sh"}
      ]
    }

每条 spec：``command``（必填，绝对路径可执行文件）、``matcher``（可选正则，仅
PreToolUse/PostToolUse 生效）、``timeout``（可选毫秒，默认 5000）。

**容错策略**：单条 spec 构造失败（路径不存在 / 不可执行 / 正则非法）→ log warning
跳过该条，继续其余。坏配置不让整个 agent 起不来——Lumi 面向非技术用户，hook 是
高级特性，一条配错不该是致命错误。

**顺序**：``build_config_hooks`` 按声明顺序构造各事件的 config hook 列表；dispatch
在运行时把 config hook 整体排在 builtin 之前（``[配置_1, ..., 配置_N, builtin]``）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

from lumi.agents.core.hooks.exec_shell import DEFAULT_TIMEOUT_MS, make_shell_hook
from lumi.agents.core.hooks.schema import Hook, HookEvent
from lumi.utils.jsonc import parse_jsonc
from lumi.utils.logger import logger

_VALID_EVENTS: frozenset[str] = frozenset(get_args(HookEvent))


def _hooks_config_paths(project_dir: Path, user_config_dir: Path | None) -> list[Path]:
    user_dir = user_config_dir or (Path.home() / ".lumi")
    return [
        user_dir / "hooks.json",
        project_dir / ".lumi" / "hooks.json",
        project_dir / ".lumi" / "hooks.local.json",
    ]


def _read_specs(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """读单个 hooks.json，展平为 ``[(event, spec_dict), ...]``。文件缺失 / 格式错返回空。"""
    if not path.exists():
        return []
    try:
        data = parse_jsonc(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[hooks] 读取 %s 失败，跳过: %s", path, e)
        return []
    if not isinstance(data, dict):
        logger.warning("[hooks] %s 顶层应为对象（event → 数组），跳过", path)
        return []

    specs: list[tuple[str, dict[str, Any]]] = []
    for event_name, entries in data.items():
        if event_name not in _VALID_EVENTS:
            logger.warning("[hooks] %s 中未知事件 %s，跳过", path, event_name)
            continue
        if not isinstance(entries, list):
            logger.warning("[hooks] %s 的 %s 应为数组，跳过", path, event_name)
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("command"):
                specs.append((event_name, entry))
            else:
                logger.warning(
                    "[hooks] %s 的 %s 含无效条目，跳过: %r", path, event_name, entry
                )
    return specs


def build_config_hooks(
    project_dir: Path, user_config_dir: Path | None = None
) -> dict[HookEvent, list[Hook]]:
    """读三级 ``hooks.json``，构造（不注册）项目级 config hook 字典。

    纯函数式：返回 ``{event: [hook, ...]}`` 供调用方按会话持有，再经
    ``set_run_config_hooks`` 注入 per-run contextvar——项目随会话绑定，不写进程
    全局。单条失败 log + 跳过，不中断。同事件内保持声明顺序。
    """
    # 按文件优先级从低到高收集所有 spec（声明顺序 = 收集顺序）
    by_event: dict[str, list[dict[str, Any]]] = {}
    for path in _hooks_config_paths(project_dir, user_config_dir):
        for event_name, spec in _read_specs(path):
            by_event.setdefault(event_name, []).append(spec)

    result: dict[HookEvent, list[Hook]] = {}
    count = 0
    for event_name, specs in by_event.items():
        for spec in specs:  # 声明顺序
            try:
                hook = make_shell_hook(
                    event=event_name,  # type: ignore[arg-type]
                    command=spec["command"],
                    timeout_ms=int(spec.get("timeout", DEFAULT_TIMEOUT_MS)),
                    matcher=spec.get("matcher"),
                )
            except Exception as e:
                logger.warning("[hooks] 构造 %s hook 失败，跳过该条: %s", event_name, e)
                continue
            result.setdefault(event_name, []).append(hook)  # type: ignore[index]
            count += 1

    if count > 0:
        logger.info(
            "[hooks] 项目 config hooks 构造完成：%d 条 across %d 事件 (%s)",
            count,
            len(result),
            project_dir,
        )
    return result

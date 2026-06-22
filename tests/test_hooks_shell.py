"""Shell hook + 配置加载测试：protocol 协议 + 真实 subprocess + hooks.json 加载。"""

from __future__ import annotations

import json
import re

import pytest
from langchain_core.messages import HumanMessage

import lumi.agents.core.hooks.exec_shell as exec_shell
from lumi.agents.core.hooks import build_config_hooks
from lumi.agents.core.hooks.exec_shell import make_shell_hook
from lumi.agents.core.hooks.protocol import (
    matches_tool_filter,
    parse_output,
    serialize_input,
)
from lumi.agents.core.hooks.schema import AdditionalContext, Block, HookContext


def _write_script(tmp_path, body: str, name: str = "hook.sh") -> str:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


def _ctx(event="Stop", payload=None, state=None, config=None):
    return HookContext(
        state=state if state is not None else {},
        config=config if config is not None else {},
        event=event,
        payload=payload or {},
    )


@pytest.fixture(autouse=True)
def _isolate():
    # config hooks 改为返回式 build_config_hooks（无进程全局），仅需清 env 缓存
    exec_shell._env_cache = None
    yield
    exec_shell._env_cache = None


# === protocol ===


def test_serialize_input_shape():
    ctx = _ctx(
        event="PreToolUse",
        state={"messages": [HumanMessage("hi")]},
        config={"configurable": {"thread_id": "t1"}},
        payload={
            "tool_calls": [{"name": "bash", "args": {}, "id": "1"}],
            "tool_names": ["bash"],
        },
    )
    out = json.loads(serialize_input("PreToolUse", ctx))
    assert out["version"] == 1
    assert out["event"] == "PreToolUse"
    assert out["thread_id"] == "t1"
    assert out["payload"]["tool_calls"][0]["name"] == "bash"
    assert out["messages_tail"][0]["role"] == "user"


def test_parse_output_deny():
    r = parse_output('{"decision":"deny","stopReason":"no good"}', source="t")
    assert isinstance(r, Block) and r.reason == "no good"


def test_parse_output_additional_context():
    r = parse_output('{"additionalContext":"heads up"}', source="t")
    assert isinstance(r, AdditionalContext) and r.text == "heads up"


def test_parse_output_deny_overrides_additional():
    r = parse_output('{"decision":"deny","additionalContext":"x"}', source="t")
    assert isinstance(r, Block)


def test_parse_output_allow_is_none():
    assert parse_output('{"decision":"allow"}', source="t") is None


def test_parse_output_non_json_is_none():
    assert parse_output("not json at all", source="t") is None


def test_parse_output_empty_is_none():
    assert parse_output("", source="t") is None


def test_matches_tool_filter():
    pat = re.compile("bash")
    assert matches_tool_filter(pat, "PreToolUse", {"tool_calls": [{"name": "bash"}]})
    assert not matches_tool_filter(
        pat, "PreToolUse", {"tool_calls": [{"name": "read"}]}
    )
    assert matches_tool_filter(pat, "Stop", {})  # 非工具事件 matcher 无效，总命中
    assert matches_tool_filter(None, "PreToolUse", {})  # 无 pattern 总命中


# === exec_shell ===


def test_make_shell_hook_rejects_relative_path():
    with pytest.raises(ValueError):
        make_shell_hook(event="Stop", command="hook.sh")


def test_make_shell_hook_rejects_missing(tmp_path):
    with pytest.raises(ValueError):
        make_shell_hook(event="Stop", command=str(tmp_path / "nope.sh"))


async def test_shell_hook_deny(tmp_path):
    cmd = _write_script(
        tmp_path, """echo '{"decision":"deny","stopReason":"blocked by test"}'"""
    )
    hook = make_shell_hook(event="PreToolUse", command=cmd)
    r = await hook(_ctx("PreToolUse", payload={"tool_calls": [], "tool_names": []}))
    assert isinstance(r, Block) and "blocked by test" in r.reason


async def test_shell_hook_additional_context(tmp_path):
    cmd = _write_script(tmp_path, """echo '{"additionalContext":"note from hook"}'""")
    hook = make_shell_hook(event="Stop", command=cmd)
    r = await hook(_ctx("Stop"))
    assert isinstance(r, AdditionalContext) and "note from hook" in r.text


async def test_shell_hook_exit2_is_deny(tmp_path):
    cmd = _write_script(tmp_path, "echo oops >&2\nexit 2")
    hook = make_shell_hook(event="Stop", command=cmd)
    r = await hook(_ctx("Stop"))
    assert isinstance(r, Block) and "oops" in r.reason


async def test_shell_hook_allow_passthrough(tmp_path):
    cmd = _write_script(tmp_path, """echo '{"decision":"allow"}'""")
    hook = make_shell_hook(event="Stop", command=cmd)
    assert await hook(_ctx("Stop")) is None


async def test_shell_hook_timeout(tmp_path):
    cmd = _write_script(tmp_path, "sleep 2")
    hook = make_shell_hook(event="Stop", command=cmd, timeout_ms=100)
    r = await hook(_ctx("Stop"))
    assert isinstance(r, Block) and "timeout" in r.reason


async def test_shell_hook_matcher_skips_subprocess(tmp_path):
    cmd = _write_script(tmp_path, """echo '{"decision":"deny"}'""")
    hook = make_shell_hook(event="PreToolUse", command=cmd, matcher="bash")
    # 当前批次工具是 read，不匹配 bash → 跳过 subprocess，返回 None
    r = await hook(_ctx("PreToolUse", payload={"tool_calls": [{"name": "read"}]}))
    assert r is None


def test_filter_env_whitelist(monkeypatch):
    monkeypatch.setattr(exec_shell, "_env_cache", None)
    monkeypatch.setenv("LUMI_HOOK_FOO", "visible")
    monkeypatch.setenv("SECRET_KEY", "should-not-leak")
    env = exec_shell._filter_env()
    assert env.get("LUMI_HOOK_FOO") == "visible"
    assert "SECRET_KEY" not in env
    assert "PATH" in env


# === config_loader ===


def _user_dir_absent(tmp_path):
    return tmp_path / "nouser"  # 不存在，避免读真实 ~/.lumi/hooks.json


def _write_hooks_json(tmp_path, data: dict):
    (tmp_path / ".lumi").mkdir(exist_ok=True)
    (tmp_path / ".lumi" / "hooks.json").write_text(json.dumps(data))


def test_build_config_hooks_constructs_shell_hook(tmp_path):
    cmd = _write_script(tmp_path, "echo '{}'")
    _write_hooks_json(tmp_path, {"Stop": [{"command": cmd}]})
    hooks = build_config_hooks(tmp_path, user_config_dir=_user_dir_absent(tmp_path))
    assert len(hooks.get("Stop", [])) == 1
    assert "shell_hook" in hooks["Stop"][0].__name__


def test_build_config_hooks_is_pure(tmp_path):
    """返回式构造无进程全局：重复调用结果一致，不累积、不写全局。"""
    cmd = _write_script(tmp_path, "echo '{}'")
    _write_hooks_json(tmp_path, {"Stop": [{"command": cmd}]})
    user = _user_dir_absent(tmp_path)
    h1 = build_config_hooks(tmp_path, user_config_dir=user)
    h2 = build_config_hooks(tmp_path, user_config_dir=user)
    assert len(h1["Stop"]) == 1 and len(h2["Stop"]) == 1  # 不累积


def test_build_config_hooks_skips_bad_command(tmp_path):
    _write_hooks_json(tmp_path, {"Stop": [{"command": "/nonexistent/x.sh"}]})
    hooks = build_config_hooks(tmp_path, user_config_dir=_user_dir_absent(tmp_path))
    assert hooks == {}  # 坏 command 跳过，不抛


def test_build_config_hooks_skips_unknown_event(tmp_path):
    cmd = _write_script(tmp_path, "echo '{}'")
    _write_hooks_json(tmp_path, {"NotAnEvent": [{"command": cmd}]})
    hooks = build_config_hooks(tmp_path, user_config_dir=_user_dir_absent(tmp_path))
    assert hooks == {}


def test_build_config_hooks_preserves_declaration_order(tmp_path):
    c1 = _write_script(tmp_path, "echo '{}'", name="a.sh")
    c2 = _write_script(tmp_path, "echo '{}'", name="b.sh")
    _write_hooks_json(tmp_path, {"PreToolUse": [{"command": c1}, {"command": c2}]})
    hooks = build_config_hooks(tmp_path, user_config_dir=_user_dir_absent(tmp_path))
    names = [h.__name__ for h in hooks["PreToolUse"]]
    assert names == ["shell_hook_a.sh", "shell_hook_b.sh"]  # 声明顺序

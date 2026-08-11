"""授权通过后放宽工作区边界（人工审批 / auto 分类器 / privileged 三条路径）。

回归背景：审批与工作区边界是两道正交的门，授权只过第一道。边界不放宽时
write/edit 执行期的 validate_path 仍抛 PermissionError，且 default/auto 模式因
boundary_ok 恒 False 而每轮都回到裁决——连「始终允许」写入的 allow 规则都不生效。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from lumi.agents.core.nodes import auto_classify, human_approval, is_use_tool
from lumi.agents.permissions.engine import PermissionEngine
from lumi.agents.permissions.matcher import build_exact_expr
from lumi.agents.permissions.routing import route_decision
from lumi.gateway.bridge import AgentBridge
from lumi.gateway.bridge.folders import _enclosing_dir


class _FakeBroker:
    def __init__(self, decision):
        self._decision = decision

    async def request(self, payload, reject_value):
        return self._decision


@pytest.fixture
def wired(tmp_path):
    """bridge（提供 widen 回调）+ engine（提供边界）+ 假 runtime，接线同真实运行时。

    ``outside`` 是工作区外的目录，越界用例的靶子。
    """

    def _make(decision: str = "approve", tool_mode: str = "default"):
        project, outside = tmp_path / "project", tmp_path / "outside"
        project.mkdir(exist_ok=True)
        outside.mkdir(exist_ok=True)
        engine = PermissionEngine(
            project_dir=project, user_config_dir=tmp_path / "user"
        )
        bridge = AgentBridge()
        bridge._context = SimpleNamespace(permission_engine=engine)
        runtime = SimpleNamespace(
            context=SimpleNamespace(
                permission_engine=engine,
                approval_broker=_FakeBroker({"decision": decision}),
                widen_boundary=bridge._folders.widen_for_violations,
                tool_mode=tool_mode,
            )
        )
        return SimpleNamespace(
            bridge=bridge,
            engine=engine,
            runtime=runtime,
            project=project,
            outside=outside,
        )

    return _make


def _state(tool_calls):
    return {"messages": [AIMessage(content="", tool_calls=tool_calls)]}


def _call(name: str, args: dict) -> dict:
    return {"id": "tc-1", "name": name, "args": args}


def _stub_classifier(monkeypatch, decision: str) -> None:
    """替换分类器链，避免测试打真实模型。"""

    class _Chain:
        async def ainvoke(self, _payload):
            return SimpleNamespace(decision=decision, reason="test")

    monkeypatch.setattr(
        "lumi.agents.core.nodes.resolve_pointer",
        lambda _name: SimpleNamespace(model="m", conn_kwargs=lambda: {}),
    )
    monkeypatch.setattr(
        "lumi.agents.core.nodes.structured_output", lambda **_kw: _Chain()
    )


# ── _enclosing_dir：越界路径 → 应授权目录 ──


def test_enclosing_dir_existing_directory(tmp_path):
    assert _enclosing_dir(str(tmp_path)) == tmp_path.resolve()


def test_enclosing_dir_file_takes_parent(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("x")
    assert _enclosing_dir(str(f)) == tmp_path.resolve()


def test_enclosing_dir_walks_up_to_nearest_existing(tmp_path):
    """整条尾巴都不存在时取最近的已存在祖先，而非拿不存在的 parent 去授权。"""
    assert (
        _enclosing_dir(str(tmp_path / "new" / "deep" / "x.txt")) == tmp_path.resolve()
    )


def test_enclosing_dir_refuses_filesystem_root():
    """把 / 加进工作区等于关掉边界，远超「放宽该目录」的授权语义。"""
    assert _enclosing_dir("/") is None


# ── 人工审批路径 ──


async def test_human_approve_widens_boundary(wired):
    w = wired()
    target = str(w.outside / "out.txt")
    assert not w.engine.check_workspace_boundary("write", {"file_path": target})

    cmd = await human_approval(
        _state([_call("write", {"file_path": target})]), w.runtime
    )

    assert cmd.goto == "ToolExecutor"
    assert w.engine.check_workspace_boundary("write", {"file_path": target})
    assert w.outside.resolve() in w.engine.authorized_directories()
    assert w.bridge._extra_folders == [str(w.outside.resolve())]


async def test_human_reject_does_not_widen(wired):
    w = wired(decision="reject")
    target = str(w.outside / "out.txt")

    await human_approval(_state([_call("write", {"file_path": target})]), w.runtime)

    assert not w.engine.check_workspace_boundary("write", {"file_path": target})
    assert w.bridge._extra_folders == []


# ── auto 模式分类器路径 ──


async def test_classifier_approve_widens_boundary(wired, monkeypatch):
    """AI 裁决与用户授权同权：分类器放行同样放宽边界。"""
    w = wired()
    _stub_classifier(monkeypatch, "approve")
    target = str(w.outside / "out.txt")

    cmd = await auto_classify(
        _state([_call("write", {"file_path": target})]), w.runtime
    )

    assert cmd.goto == "ToolExecutor"
    assert w.engine.check_workspace_boundary("write", {"file_path": target})
    assert w.bridge._extra_folders == [str(w.outside.resolve())]


async def test_classifier_reject_does_not_widen(wired, monkeypatch):
    w = wired()
    _stub_classifier(monkeypatch, "reject")
    target = str(w.outside / "out.txt")

    cmd = await auto_classify(
        _state([_call("write", {"file_path": target})]), w.runtime
    )

    assert cmd.goto == "CallModel"
    assert not w.engine.check_workspace_boundary("write", {"file_path": target})
    assert w.bridge._extra_folders == []


# ── 端到端：两道门都通 ──


async def test_approve_unblocks_execution_time_validate_path(wired):
    """第二道门：filesystem 层的 validate_path 此前对已授权的写入照抛 PermissionError。"""
    from lumi.agents.permissions.workspace import (
        set_run_authorized_source_for,
        validate_path,
    )

    w = wired()
    set_run_authorized_source_for(w.engine)
    target = str(w.outside / "out.txt")

    await human_approval(_state([_call("write", {"file_path": target})]), w.runtime)

    assert validate_path(target) == Path(target).resolve()


async def test_approve_ends_the_approval_loop(wired):
    """授权 + 「始终允许」后同一命令直放，不再每轮回到审批。"""
    w = wired()
    args = {"command": f"mkdir {w.outside}/sub", "description": "m"}
    calls = [_call("bash", args)]
    assert route_decision(calls, "default", "normal", w.engine) == "HumanApproval"

    await human_approval(_state(calls), w.runtime)
    w.engine.add_allow_rule(build_exact_expr("bash", args))

    assert route_decision(calls, "default", "normal", w.engine) == "ToolExecutor"


async def test_classifier_approve_ends_the_auto_loop(wired, monkeypatch):
    """auto 模式：分类器放行后同一命令不再每轮重新送分类器（省一次模型调用）。

    与上一条走 route_decision 的不同分支（auto 的 all_allowed 快路径），故不合并。
    """
    w = wired()
    _stub_classifier(monkeypatch, "approve")
    args = {"command": f"mkdir {w.outside}/sub", "description": "m"}
    calls = [_call("bash", args)]
    assert route_decision(calls, "auto", "normal", w.engine) == "AutoClassify"

    await auto_classify(_state(calls), w.runtime)
    w.engine.add_allow_rule(build_exact_expr("bash", args))

    assert route_decision(calls, "auto", "normal", w.engine) == "ToolExecutor"


# ── privileged 模式：自动放行即授权，边界随之放宽 ──


def test_privileged_widens_for_write(wired):
    """privileged 既不审批也不过分类器，放行时就得放宽，否则 write 仍被 validate_path 拒。"""
    w = wired(tool_mode="privileged")
    target = str(w.outside / "out.txt")

    assert is_use_tool(_state([_call("write", {"file_path": target})]), w.runtime) == (
        "ToolExecutor"
    )
    assert w.engine.check_workspace_boundary("write", {"file_path": target})


def test_default_mode_routing_does_not_widen(wired):
    """default 模式的放宽只发生在审批之后，不在路由这一步。"""
    w = wired()
    target = str(w.outside / "out.txt")

    assert is_use_tool(_state([_call("write", {"file_path": target})]), w.runtime) == (
        "HumanApproval"
    )
    assert not w.engine.check_workspace_boundary("write", {"file_path": target})
    assert w.bridge._extra_folders == []


# ── 放宽面收窄：越界但非「本机路径写操作」的调用不换来该目录的写权 ──


@pytest.mark.parametrize(
    "make_call, why",
    [
        (
            lambda d: _call("read", {"file_path": str(d / "secret.txt")}),
            "只读本就免边界；为它放宽等于顺带开放该目录的写权限",
        ),
        (
            lambda d: _call(
                "bash", {"command": f"cat {d}/secret.txt", "description": "c"}
            ),
            "只读 bash 命中 is_write_tool 的 False 分支，同样不放宽",
        ),
        (
            lambda d: _call("db_query", {"path": str(d / "app.sqlite")}),
            "MCP 等外部工具的 path 含义未知（可能是 URL / 库名），且 is_write_tool 对未知工具 fail-closed 恒 True",
        ),
    ],
    ids=["readonly-tool", "readonly-bash", "unknown-tool"],
)
def test_privileged_does_not_widen_for_non_local_writes(wired, make_call, why):
    w = wired(tool_mode="privileged")

    assert is_use_tool(_state([make_call(w.outside)]), w.runtime) == "ToolExecutor", why
    assert w.bridge._extra_folders == [], why
    assert not w.engine.check_workspace_boundary(
        "write", {"file_path": str(w.outside / "pwn.txt")}
    ), why


async def test_mixed_batch_does_not_widen_for_read(wired):
    """批次是混合的（纯只读批次更早短路）。批准含越界 read 的批次不开该目录写权。"""
    w = wired()

    await human_approval(
        _state(
            [
                _call("read", {"file_path": str(w.outside / "secret.txt")}),
                _call("write", {"file_path": str(w.project / "ok.md"), "content": "x"}),
            ]
        ),
        w.runtime,
    )

    assert w.bridge._extra_folders == []
    assert not w.engine.check_workspace_boundary(
        "write", {"file_path": str(w.outside / "pwn.txt")}
    )

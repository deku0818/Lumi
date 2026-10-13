"""AgentBridge.set_workspace 单元测试（不初始化真实 Agent，纯路径断言）"""

from pathlib import Path

import pytest

from lumi.gateway.bridge import AgentBridge


async def test_set_workspace_does_not_change_process_cwd(tmp_path):
    """项目随会话绑定：set_workspace 返回目标项目，但不动进程级 cwd（不影响其它会话）。"""
    old_cwd = Path.cwd()
    bridge = AgentBridge()
    result = await bridge.folders.set_workspace(str(tmp_path))
    assert result["workspace"] == str(tmp_path.resolve())
    assert Path.cwd() == old_cwd  # 关键：不再 os.chdir


async def test_set_workspace_rejects_missing_dir(tmp_path):
    bridge = AgentBridge()
    with pytest.raises(ValueError):
        await bridge.folders.set_workspace(str(tmp_path / "nope"))


async def test_close_reaps_thread_shell():
    """回归：断连（bridge.close）回收本会话 thread 的持久 shell，否则长跑 serve 泄漏 bash 进程。"""
    from langchain_core.runnables.config import RunnableConfig

    from lumi.agents.runtime.shell_session import get_shell_session_manager

    bridge = AgentBridge()  # 不 initialize：_agent 为 None，close 跳过 aclose
    bridge._config = RunnableConfig(configurable={"thread_id": "t-reap"})
    mgr = get_shell_session_manager()
    mgr.get_session("t-reap", working_dir="/tmp")
    assert "t-reap" in mgr._sessions
    await bridge.close()
    assert "t-reap" not in mgr._sessions  # 断连即回收


def test_injected_note_marked_structurally():
    """注入块打 injected_prefix 计数，显示侧按结构掉块——不依赖正则识别正文。"""
    from langchain_core.messages import HumanMessage

    from lumi.agents.core.meta_message import visible_user_text
    from lumi.agents.core.node_helpers.messages import inject_text_into_message

    msg = HumanMessage(content="你好")
    # 注入多条 note，且刻意不带 <system-reminder> 标签，
    # 证明显示侧走的是计数掉块而非正则
    msg = inject_text_into_message(msg, "note-a\n")
    msg = inject_text_into_message(msg, "note-b\n")
    assert msg.additional_kwargs["injected_prefix"] == 2
    assert msg.content[0] == {"type": "text", "text": "note-b\n"}
    assert visible_user_text(msg) == "你好"

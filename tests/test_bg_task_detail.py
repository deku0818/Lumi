"""后台任务卡片信息量：prompt 上 wire、输出尾部读取、后台 agent 活动汇报。"""

import asyncio
from contextlib import suppress
from pathlib import Path

from lumi.agents.runtime.bg_tasks import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
    read_output_tail,
    serialize_task,
)


def _entry(tmp_path: Path, prompt: str = "") -> BackgroundTaskEntry:
    return BackgroundTaskEntry(
        task_id="bg_test",
        kind=TaskKind.AGENT,
        status=TaskStatus.RUNNING,
        label="agent:explore",
        started_at=0.0,
        output_file=tmp_path / "bg_test.txt",
        agent_name="explore",
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# serialize_task：prompt 上 wire（截断）
# ---------------------------------------------------------------------------


def test_prompt_reaches_wire(tmp_path):
    data = serialize_task(_entry(tmp_path, "分析权限系统"))
    assert data["prompt"] == "分析权限系统"
    assert "async_task" not in data


def test_long_prompt_truncated(tmp_path):
    """bg_tasks.update 是全量快照广播，单条 prompt 无界会被任务数 × 变更频率放大。"""
    data = serialize_task(_entry(tmp_path, "x" * 3000))
    assert len(data["prompt"]) == 1001
    assert data["prompt"].endswith("…")


def test_prompt_under_limit_not_truncated(tmp_path):
    """常见长度的任务描述整段上 wire——卡片可展开看全文，截断只兜住异常大的。"""
    data = serialize_task(_entry(tmp_path, "y" * 900))
    assert data["prompt"] == "y" * 900


# ---------------------------------------------------------------------------
# read_output_tail
# ---------------------------------------------------------------------------


def test_read_output_tail_whole_small_file(tmp_path):
    f = tmp_path / "out.txt"
    f.write_text("line1\nline2\n", encoding="utf-8")
    text, size, truncated = read_output_tail(f)
    assert text == "line1\nline2\n"
    assert size == 12
    assert truncated is False


def test_read_output_tail_drops_partial_first_line(tmp_path):
    """截断落点多半在行中间，残行要掐掉，否则预览首行是半截字符串。"""
    f = tmp_path / "out.txt"
    f.write_text("aaaa\nbbbb\ncccc\n", encoding="utf-8")
    text, size, truncated = read_output_tail(f, limit=8)  # 末 8 字节 = "bb\ncccc\n"
    assert text == "cccc\n"
    assert truncated is True
    assert size == 15


def test_read_output_tail_missing_file(tmp_path):
    """agent 任务运行中输出文件还不存在——空文本，不是错误。"""
    assert read_output_tail(tmp_path / "nope.txt") == ("", 0, False)


# ---------------------------------------------------------------------------
# 进度快照里的可增长字段都必须有界（同一份快照会被反复广播给每条连接）
# ---------------------------------------------------------------------------


def test_workflow_last_log_capped():
    """log() 同时是脚本里 print 的别名——print(整个文件) 不能把全文塞进每次广播。"""
    from lumi.agents.core.workflow.engine import _LAST_LOG_LIMIT, WorkflowEngine

    engine = WorkflowEngine("export const meta = {name:'x',description:'x'}")
    seen: list[dict] = []
    engine.set_progress_sink(seen.append)
    engine._log("z" * 5000)

    assert len(seen[-1]["last_log"]) == _LAST_LOG_LIMIT


async def test_workflow_task_carries_description_as_intent(tmp_path, monkeypatch):
    """workflow 卡片的「任务内容」取工具的 description —— name 只是个 slug。"""
    from lumi.agents.core.workflow.engine import WorkflowEngine
    from lumi.agents.tools.providers import workflow as wf

    monkeypatch.setattr(wf, "bg_tasks_dir", lambda: tmp_path)
    engine = WorkflowEngine("export const meta = {name:'x',description:'x'}")
    entry = wf._start_workflow_task(
        "review", engine, "审查当前分支改动的正确性与测试覆盖"
    )

    assert entry.prompt == "审查当前分支改动的正确性与测试覆盖"
    entry.async_task.cancel()  # fire-and-forget 的执行体，本测试只关心注册的元数据
    with suppress(asyncio.CancelledError):
        await entry.async_task


def test_notify_progress_skips_identical_snapshot(tmp_path):
    """每次 _fire_change 都是一次全量快照广播；后台 agent 逐超步上报时首尾几步是重复的。"""
    from lumi.agents.runtime.bg_tasks import get_task_registry

    registry = get_task_registry()
    entry = _entry(tmp_path)
    registry.register(entry)
    fired: list[int] = []
    registry.set_on_change(lambda: fired.append(1))

    registry.notify_progress(entry.task_id, {"tool": "grep", "tools_done": 0})
    registry.notify_progress(entry.task_id, {"tool": "grep", "tools_done": 0})
    assert len(fired) == 1

    registry.notify_progress(entry.task_id, {"tool": None, "tools_done": 1})
    assert len(fired) == 2

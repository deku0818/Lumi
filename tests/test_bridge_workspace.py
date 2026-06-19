"""AgentBridge.set_workspace 单元测试（不初始化真实 Agent，纯路径断言）"""

import os
from pathlib import Path

import pytest

from lumi.gateway.bridge import AgentBridge


async def test_set_workspace_switches_cwd(tmp_path):
    old_cwd = Path.cwd()
    bridge = AgentBridge()
    try:
        result = await bridge.set_workspace(str(tmp_path))
        assert result["workspace"] == str(tmp_path.resolve())
        assert Path.cwd() == tmp_path.resolve()
    finally:
        os.chdir(old_cwd)


async def test_set_workspace_rejects_missing_dir(tmp_path):
    bridge = AgentBridge()
    with pytest.raises(ValueError):
        await bridge.set_workspace(str(tmp_path / "nope"))


async def test_set_workspace_preserves_extra_folders(tmp_path):
    """切目录后本会话临时目录保留，且 _notified_folders 不脱节（无虚假移除提醒）。"""
    old_cwd = Path.cwd()
    extra = tmp_path / "extra"
    extra.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    bridge = AgentBridge()
    bridge.add_folder(str(extra))
    bridge._drain_folder_note()  # 已告知模型，快照对齐
    try:
        await bridge.set_workspace(str(dest))
        assert bridge._extra_folders == [str(extra.resolve())]
        # 临时目录未变 → 不产生任何增减提醒
        assert bridge._drain_folder_note() == ""
    finally:
        os.chdir(old_cwd)


def test_add_remove_folder(tmp_path):
    bridge = AgentBridge()
    extra = tmp_path / "extra"
    extra.mkdir()
    resolved = str(extra.resolve())
    assert bridge.add_folder(str(extra))["folders"] == [resolved]
    # 重复添加去重
    assert bridge.add_folder(str(extra))["folders"] == [resolved]
    assert bridge.remove_folder(str(extra))["folders"] == []


def test_add_folder_rejects_missing_dir(tmp_path):
    bridge = AgentBridge()
    with pytest.raises(ValueError):
        bridge.add_folder(str(tmp_path / "nope"))


def test_folder_note_add_then_remove_lifecycle(tmp_path):
    bridge = AgentBridge()
    extra = tmp_path / "extra"
    extra.mkdir()
    resolved = str(extra.resolve())

    # 添加 → 下一次 drain 产出添加提醒
    bridge.add_folder(str(extra))
    note = bridge._drain_folder_note()
    assert resolved in note and "添加" in note
    # 无新变更 → 空串（提醒只发一次）
    assert bridge._drain_folder_note() == ""

    # 移除 → 中性措辞的移除提醒
    bridge.remove_folder(str(extra))
    note = bridge._drain_folder_note()
    assert resolved in note and "移除" in note
    assert "不应" not in note


def test_folder_note_add_remove_cancels_out(tmp_path):
    bridge = AgentBridge()
    extra = tmp_path / "extra"
    extra.mkdir()
    bridge.add_folder(str(extra))
    bridge.remove_folder(str(extra))
    # 消息发出前加了又删 → 抵消，不打扰模型
    assert bridge._drain_folder_note() == ""


def test_prepend_reminder_handles_both_content_forms():
    from lumi.gateway.bridge import prepend_reminder

    assert prepend_reminder("你好", "<system-reminder>x</system-reminder>\n") == (
        "<system-reminder>x</system-reminder>\n你好"
    )
    blocks = prepend_reminder([{"type": "text", "text": "你好"}], "note\n")
    assert blocks[0] == {"type": "text", "text": "note\n"}
    assert blocks[1] == {"type": "text", "text": "你好"}

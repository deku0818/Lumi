"""ToolBlock 懒渲染测试"""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Collapsible

from lumi.tui.theme import APP_CSS, LUMI_DARK_THEME
from lumi.tui.widgets.tool_block import ToolBlock, ToolStatus


class ToolBlockTestApp(App):
    CSS = APP_CSS

    def __init__(self):
        super().__init__()
        self.register_theme(LUMI_DARK_THEME)
        self.theme = "lumi-dark"

    def compose(self) -> ComposeResult:
        yield ToolBlock("bash", {"command": "ls -la"})


@pytest.mark.asyncio
async def test_set_done_stores_output():
    """set_done 应存储 output 到 _output_text"""
    async with ToolBlockTestApp().run_test() as pilot:
        block = pilot.app.query_one(ToolBlock)
        block.set_done("hello world")
        assert block._output_text == "hello world"
        assert block._status == ToolStatus.DONE


@pytest.mark.asyncio
async def test_compose_no_tool_output_widget():
    """compose 时不应创建 .tool-output widget"""
    async with ToolBlockTestApp().run_test() as pilot:
        block = pilot.app.query_one(ToolBlock)
        assert len(block.query(".tool-output")) == 0


@pytest.mark.asyncio
async def test_expand_mounts_output():
    """展开后应懒渲染 output widget"""
    async with ToolBlockTestApp().run_test() as pilot:
        block = pilot.app.query_one(ToolBlock)
        block.set_done("test output content")

        assert len(block.query(".tool-output")) == 0

        block.query_one(Collapsible).collapsed = False
        await pilot.pause()
        await pilot.pause()

        assert len(block.query(".tool-output")) == 1


@pytest.mark.asyncio
async def test_collapse_destroys_output():
    """折叠后应销毁 output widget"""
    async with ToolBlockTestApp().run_test() as pilot:
        block = pilot.app.query_one(ToolBlock)
        block.set_done("test output content")

        collapsible = block.query_one(Collapsible)
        collapsible.collapsed = False
        await pilot.pause()
        await pilot.pause()
        assert len(block.query(".tool-output")) == 1

        collapsible.collapsed = True
        await pilot.pause()
        await pilot.pause()
        assert len(block.query(".tool-output")) == 0


@pytest.mark.asyncio
async def test_expand_with_error_text():
    """set_error 后展开应显示错误文本"""
    async with ToolBlockTestApp().run_test() as pilot:
        block = pilot.app.query_one(ToolBlock)
        block.set_error("something went wrong")

        block.query_one(Collapsible).collapsed = False
        await pilot.pause()
        await pilot.pause()

        assert len(block.query(".tool-output")) == 1


@pytest.mark.asyncio
async def test_no_duplicate_on_rapid_toggle():
    """快速切换不会产生重复 output widget"""
    async with ToolBlockTestApp().run_test() as pilot:
        block = pilot.app.query_one(ToolBlock)
        block.set_done("test")

        collapsible = block.query_one(Collapsible)
        collapsible.collapsed = False
        await pilot.pause()
        collapsible.collapsed = True
        await pilot.pause()
        collapsible.collapsed = False
        await pilot.pause()
        await pilot.pause()

        assert len(block.query(".tool-output")) <= 1

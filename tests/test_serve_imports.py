"""回归：lumi serve 的导入顺序不能有循环导入。

serve 入口按 gateway.channels.ws → bootstrap → cron.runtime → scheduler 的顺序加载；
scheduler 顶层若 import permissions/core，会触发 permissions/__init__ → engine → tools
→ providers.cron → scheduler（部分初始化）的环。该环只在 serve 的导入顺序下触发，普通
pytest 的导入顺序（permissions 往往已先加载）不复现，故用全新解释器验证。
"""

from __future__ import annotations

import subprocess
import sys


def test_serve_entry_imports_without_cycle():
    """全新解释器里导入 serve 入口，复现 serve 的真实导入顺序，确保无循环导入。"""
    result = subprocess.run(
        [sys.executable, "-c", "import lumi.gateway.channels.ws"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"serve 入口导入失败（疑似循环导入）：\n{result.stderr}"
    )

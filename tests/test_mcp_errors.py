"""MCP 连接失败时给用户看得懂的原因。"""

from __future__ import annotations

import sys

from lumi.agents.tools.providers.mcp.pool import format_exception_details
from lumi.agents.tools.providers.mcp.probe import test_mcp_server as probe


def test_nested_exception_groups_report_leaves():
    # anyio 两层 TaskGroup 包出来的只有「unhandled errors in a TaskGroup (1 sub-exception)」
    err = ExceptionGroup(
        "outer", [ExceptionGroup("inner", [OSError("Connection closed")])]
    )
    assert format_exception_details(err) == "OSError: Connection closed"


async def test_probe_error_includes_server_stderr(tmp_path):
    # server 自己在 stderr 说了原因（缺密钥等）就退出：此前三处都看不到这句
    script = tmp_path / "srv.py"
    script.write_text(
        "import sys; print('MISSING API KEY', file=sys.stderr); sys.exit(1)"
    )
    result = await probe(
        {"transport": "stdio", "command": sys.executable, "args": [str(script)]},
        timeout=10,
    )
    assert result["ok"] is False
    assert "MISSING API KEY" in result["error"]

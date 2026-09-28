"""lumi serve 的令牌：经环境变量传入后不留在进程环境里。"""

from __future__ import annotations

import os

from typer.testing import CliRunner

from lumi import cli


def test_token_env_is_consumed(monkeypatch, isolated_config):
    # 令牌走 env 而非 argv（argv 对本机其它用户可见）；读完即从环境删掉，否则 bash /
    # MCP 等子进程全都继承这枚能驱动 agent 的令牌
    from lumi.gateway.channels import ws

    seen: dict = {}
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **kw: seen.update(env=dict(os.environ))
    )
    monkeypatch.setattr(cli, "_export_lumi_bin", lambda: None)
    monkeypatch.setattr("lumi.gateway.toolbox.inject_path", lambda: None)
    monkeypatch.setenv("LUMI_TOKEN", "s3cret")
    result = CliRunner().invoke(cli.app, ["serve"])
    assert result.exit_code == 0, result.output
    assert ws.app.state.token == "s3cret"
    assert "LUMI_TOKEN" not in seen["env"]


def test_uvicorn_log_lines_redact_token():
    # uvicorn 的 WS 握手行与访问行都把 ?token= 原样写进日志（serve 实测复现过）
    import logging

    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        "",
        0,
        '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:1", "/ws?token=abc&thread=t"),
        None,
    )
    assert cli._redact_token(record)
    assert (
        record.getMessage()
        == '127.0.0.1:1 - "WebSocket /ws?token=***&thread=t" [accepted]'
    )

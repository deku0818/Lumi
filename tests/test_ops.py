"""运维命令的纯函数部分：装法判定、升级命令、探测结论、日志取尾。"""

import sys

import pytest

from lumi import ops

# ── 装法判定 ──────────────────────────────────────────────────────────────


def test_uv_tool_detected_by_receipt(tmp_path, monkeypatch):
    # uv 在工具 venv 根留 uv-receipt.toml；认落款而非认 ~/.local/share/uv/tools 路径，
    # 因为 UV_TOOL_DIR 能把工具目录整体改道
    (tmp_path / "uv-receipt.toml").write_text("")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert ops.install_kind() == "uv-tool"


def test_pipx_detected_by_metadata(tmp_path, monkeypatch):
    (tmp_path / "pipx_metadata.json").write_text("{}")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert ops.install_kind() == "pipx"


def test_plain_pip_when_no_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(ops, "_editable", lambda: False)
    assert ops.install_kind() == "pip"


def test_editable_is_source(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(ops, "_editable", lambda: True)
    assert ops.install_kind() == "source"


def test_frozen_wins_over_everything(tmp_path, monkeypatch):
    # 桌面打包版：venv 落款可能什么都不像，但它绝不该自己动包
    (tmp_path / "uv-receipt.toml").write_text("")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert ops.install_kind() == "frozen"


def test_editable_reads_real_dist_info():
    # 拿真实 dist-info 试一次：本仓库自身就是可编辑安装（uv sync 的装法）。
    # 构造的假 direct_url.json 证明不了字段路径写对了，真文件能
    assert ops._editable() is True


# ── 升级命令 ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_uv(monkeypatch):
    monkeypatch.setattr(ops, "_uv_path", lambda: "/fake/uv")


def test_uv_latest_clears_version_pin(fake_uv):
    # 装过时带了 ==X 的话，`uv tool upgrade` 会一声不吭什么都不做还退 0（实测
    # "Nothing to upgrade"），上层据此报「升级完成」就是假成功。@latest 重新解析才越得过
    assert ops.upgrade_command("uv-tool", "") == [
        "/fake/uv",
        "tool",
        "install",
        "--force",
        "lumi-harness@latest",
    ]


def test_uv_pinned_version(fake_uv):
    assert ops.upgrade_command("uv-tool", "0.2.119") == [
        "/fake/uv",
        "tool",
        "install",
        "--force",
        "lumi-harness==0.2.119",
    ]


def test_pipx_shapes():
    assert ops.upgrade_command("pipx", "") == [
        "pipx",
        "install",
        "--force",
        "lumi-harness",
    ]
    assert ops.upgrade_command("pipx", "1.0")[-1] == "lumi-harness==1.0"


def test_pip_uses_this_interpreter():
    cmd = ops.upgrade_command("pip", "")
    assert cmd[:4] == [sys.executable, "-m", "pip", "install"]


def test_no_manager_upgrade_subcommand(fake_uv):
    """任何一条升级命令都不许用 upgrade 子命令——它遇到版本 pin 会静默空转。"""
    for kind in ("uv-tool", "pipx", "pip"):
        for target in ("", "0.2.119"):
            assert "upgrade" not in ops.upgrade_command(kind, target)


# ── 探测结论 ──────────────────────────────────────────────────────────────


def test_running_covers_both_live_states():
    # 无鉴权也是「在跑」——它是最该被喊出来的那种在跑，不是一种「没在跑」
    assert ops.ServiceStatus("guarded").running
    assert ops.ServiceStatus("unguarded").running
    assert not ops.ServiceStatus("down").running
    assert not ops.ServiceStatus("error").running


def _replies(*outcomes):
    """依次把给定探测结果喂给 check_service。"""
    queue = iter(outcomes)

    async def fake_probe(_url):
        return next(queue), ""

    return fake_probe


def test_bogus_token_probe_classifies(monkeypatch):
    for outcome, state in (
        ("rejected", "guarded"),
        ("ok", "unguarded"),
        ("down", "down"),
        ("error", "error"),
    ):
        monkeypatch.setattr(ops, "_probe", _replies(outcome))
        assert ops.check_service("127.0.0.1", 8765).state == state


def test_wrong_token_reported_as_error(monkeypatch):
    # 负向探针说「鉴权生效」，正向探针又被拒 → 是你手上的令牌不对，不是服务坏了
    monkeypatch.setattr(ops, "_probe", _replies("rejected", "rejected"))
    status = ops.check_service("127.0.0.1", 8765, token="wrong")
    assert status.state == "error"
    assert "令牌" in status.detail


def test_right_token_confirms_service_works(monkeypatch):
    monkeypatch.setattr(ops, "_probe", _replies("rejected", "ok"))
    status = ops.check_service("127.0.0.1", 8765, token="right")
    assert status.state == "guarded"
    assert "list_sessions" in status.detail


# ── 日志取尾 ──────────────────────────────────────────────────────────────


def test_tail_returns_last_lines(tmp_path):
    log = tmp_path / "Lumi.log"
    log.write_text("\n".join(f"line-{i}" for i in range(500)) + "\n")
    assert ops.tail(log, 3) == ["line-497", "line-498", "line-499"]


def test_tail_handles_short_file(tmp_path):
    log = tmp_path / "Lumi.log"
    log.write_text("only\n")
    assert ops.tail(log, 50) == ["only"]

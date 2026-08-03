"""office_rpc 单测：分支覆盖（未装 / 缓存命中 / 旧产物清理 / 不支持类型），不跑真 officecli。"""

import os
import subprocess

import pytest

from lumi.gateway import office_rpc, toolbox
from lumi.gateway.office_rpc import render_office
from lumi.gateway.toolbox import ToolStatus


def _ok_run(cmd, **kwargs):
    """officecli 成功 stub：向 -o 路径写出完整 HTML。"""
    out = cmd[cmd.index("-o") + 1]
    with open(out, "w") as f:
        f.write("<html>ok</html>")
    return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def office_env(isolated_config, tmp_path, monkeypatch):
    """共享配置隔离（缓存随之进 tmp），officecli 默认可用。"""
    # render_office 用 locate（不跑 --version），故 stub locate 而非 detect
    monkeypatch.setattr(
        toolbox,
        "locate",
        lambda name: ToolStatus(name, "toolbox", "", "/fake/officecli"),
    )
    monkeypatch.setattr(office_rpc, "_NEED_INVARIANT", False)
    src = tmp_path / "报告.docx"
    src.write_bytes(b"fake docx")
    return {"src": src}


def test_unsupported_ext(office_env, tmp_path):
    other = tmp_path / "a.doc"
    other.write_bytes(b"x")
    assert render_office(str(other)) == {"ok": False, "reason": "unsupported"}


def test_source_gone(office_env, tmp_path):
    result = render_office(str(tmp_path / "不存在.docx"))
    assert result["ok"] is False
    assert result["reason"] == "error"


def test_missing_cli(office_env, monkeypatch):
    monkeypatch.setattr(toolbox, "locate", lambda name: ToolStatus(name, "missing"))
    assert render_office(str(office_env["src"])) == {"ok": False, "reason": "missing"}


def test_render_writes_and_cleans_stale(office_env, monkeypatch):
    """转换产物落缓存；同路径旧 mtime 产物被清掉。"""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _ok_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    cache = office_rpc._cache_dir()
    cache.mkdir(parents=True)
    stale = cache / "deadbeef-1.html"
    stale.write_text("old")

    result = render_office(str(office_env["src"]))
    assert result["ok"] is True
    html = result["html_path"]
    assert html.endswith(".html")
    assert stale.exists()  # 别人的哈希前缀不动

    # 同一路径 mtime 变化 → 新产物生成、旧产物清理
    old_html = html
    office_env["src"].write_bytes(b"changed")
    result2 = render_office(str(office_env["src"]))
    assert result2["html_path"] != old_html
    assert not os.path.exists(old_html)
    assert len(calls) == 2


def test_cache_hit_skips_run(office_env, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _ok_run)
    first = render_office(str(office_env["src"]))

    def boom(cmd, **kwargs):
        raise AssertionError("缓存命中不应再跑转换")

    monkeypatch.setattr(subprocess, "run", boom)
    second = render_office(str(office_env["src"]))
    assert second == first


def test_xlsx_gets_resize_script_docx_not(office_env, monkeypatch, tmp_path):
    """xlsx 产物注入列宽拖拽脚本；docx 保持原样。"""
    monkeypatch.setattr(subprocess, "run", _ok_run)
    xlsx = tmp_path / "表.xlsx"
    xlsx.write_bytes(b"x")
    result = render_office(str(xlsx))
    assert "col-resize" in open(result["html_path"], encoding="utf-8").read()

    result = render_office(str(office_env["src"]))  # .docx
    assert "col-resize" not in open(result["html_path"], encoding="utf-8").read()


def test_icu_missing_falls_back_to_invariant(office_env, monkeypatch):
    """无 libicu 主机（slim 容器）：首跑 Abort 带 ICU 报错 → 置 invariant 环境重试成功。"""
    calls = []

    def fake_run(cmd, env=None, **kwargs):
        calls.append(env)
        if env is None:
            return subprocess.CompletedProcess(
                cmd, 134, "", "Couldn't find a valid ICU package installed"
            )
        out = cmd[cmd.index("-o") + 1]
        with open(out, "w") as f:
            f.write("<html>ok</html>")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = render_office(str(office_env["src"]))
    assert result["ok"] is True
    assert len(calls) == 2
    assert calls[1]["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] == "1"

    # 结论记住：同进程后续渲染直接带 invariant 环境，不再双跑
    office_env["src"].write_bytes(b"changed")
    result = render_office(str(office_env["src"]))
    assert result["ok"] is True
    assert len(calls) == 3
    assert calls[2] is not None


def test_cli_failure_surfaces_message(office_env, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "boom: bad file"),
    )
    result = render_office(str(office_env["src"]))
    assert result["ok"] is False
    assert result["reason"] == "error"
    assert "boom" in result["message"]


def test_failed_render_leaves_no_cache(office_env, monkeypatch):
    """失败（含写了半截的产物）不落缓存：下次调用重跑转换而非命中残留。"""
    calls = []

    def half_then_ok(cmd, **kwargs):
        calls.append(cmd)
        out = cmd[cmd.index("-o") + 1]
        with open(out, "w") as f:
            f.write("<html>部分")  # 失败前已写出半截
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 134, "", "Aborted")
        with open(out, "w") as f:
            f.write("<html>ok</html>")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", half_then_ok)
    first = render_office(str(office_env["src"]))
    assert first["ok"] is False
    assert not list(office_rpc._cache_dir().glob("*.html"))  # 半截产物没进缓存

    second = render_office(str(office_env["src"]))
    assert second["ok"] is True
    assert len(calls) == 2


def test_timeout_cleans_up(office_env, monkeypatch):
    """超时：返回明确错误、不留 .part 残留、不落缓存。"""

    def timeout_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 120)

    monkeypatch.setattr(subprocess, "run", timeout_run)
    result = render_office(str(office_env["src"]))
    assert result["ok"] is False
    assert "超时" in result["message"]
    assert not list(office_rpc._cache_dir().iterdir())

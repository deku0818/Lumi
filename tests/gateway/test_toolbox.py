"""toolbox 单测：探测优先级 / URL 矩阵 / 解压 / 技能同步 / PATH 注入。

不真下载、不跑真实外部命令：网络与 lark-cli 全部 mock，解压用本地 fixture。
"""

import io
import json
import os
import tarfile

import pytest

from lumi.gateway import toolbox
from lumi.gateway.toolbox import (
    CORE_TOOLS,
    ToolStatus,
    detect,
    download_url,
    inject_path,
    install_missing,
    lark_skill_versions,
    skills_status,
    sync_lark_skills,
)
from lumi.utils.config import LumiConfig
from lumi.utils.read_config import get_config


@pytest.fixture
def toolbox_env(tmp_path, monkeypatch):
    """隔离配置目录 + 干净 PATH（一个空的系统 bin 目录可控注入）。"""
    config_dir = tmp_path / "lumi-config"
    config_dir.mkdir()
    system_bin = tmp_path / "system-bin"
    system_bin.mkdir()
    monkeypatch.setenv("PATH", str(system_bin))
    LumiConfig.get_instance(str(config_dir), reset=True)
    yield {"config": config_dir, "system_bin": system_bin}
    LumiConfig.reset_instance()


def _fake_exe(directory, name, version="9.9.9"):
    path = directory / name
    path.write_text(f'#!/bin/sh\necho "{name} {version}"\n')
    path.chmod(0o755)
    return path


# ── download_url 全平台矩阵 ──


@pytest.mark.parametrize(
    "tool,os_name,arch,expect",
    [
        ("uv", "darwin", "arm64", "uv-aarch64-apple-darwin.tar.gz"),
        ("uv", "linux", "x64", "uv-x86_64-unknown-linux-musl.tar.gz"),
        ("uv", "win", "x64", "uv-x86_64-pc-windows-msvc.zip"),
        ("rg", "darwin", "x64", "ripgrep-15.2.0-x86_64-apple-darwin.tar.gz"),
        ("rg", "linux", "arm64", "ripgrep-15.2.0-aarch64-unknown-linux-gnu.tar.gz"),
        ("node", "darwin", "arm64", "node-v24.18.0-darwin-arm64.tar.gz"),
        ("node", "win", "x64", "node-v24.18.0-win-x64.zip"),
    ],
)
def test_download_url_matrix(tool, os_name, arch, expect):
    url = download_url(tool, os_name, arch)
    assert url.endswith(expect)
    assert url.startswith("https://")


def test_download_url_unknown_tool():
    with pytest.raises(ValueError):
        download_url("ffmpeg", "darwin", "arm64")


# ── 探测优先级 ──


def test_detect_missing(toolbox_env):
    assert detect("uv") == ToolStatus("uv", "missing")


def test_detect_system_wins_over_toolbox(toolbox_env):
    """系统 PATH 与工具箱同时存在时，system 优先（永不遮蔽用户自装）。"""
    _fake_exe(toolbox_env["system_bin"], "rg", "14.1.1")
    box = get_config().bin_dir
    box.mkdir(parents=True)
    _fake_exe(box, "rg", "15.2.0")
    inject_path()
    status = detect("rg")
    assert status.source == "system"
    assert status.version == "14.1.1"


def test_detect_toolbox_after_inject(toolbox_env):
    box = get_config().bin_dir
    box.mkdir(parents=True)
    _fake_exe(box, "uv", "0.11.32")
    inject_path()
    status = detect("uv")
    assert status.source == "toolbox"
    assert status.version == "0.11.32"


def test_detect_symlinked_tool_is_toolbox(toolbox_env):
    """bin_dir 里的 symlink 工具（node/lark-cli 形态）必须判为 toolbox，不能被 resolve 到树内误判 system。"""
    tree_bin = toolbox_env["config"] / "node" / "bin"
    tree_bin.mkdir(parents=True)
    real = _fake_exe(tree_bin, "node", "24.18.0")
    box = get_config().bin_dir
    box.mkdir(parents=True)
    (box / "node").symlink_to(real)
    inject_path()
    status = detect("node")
    assert status.source == "toolbox"
    assert status.version == "24.18.0"


def test_detect_toolbox_without_inject(toolbox_env):
    """PATH 未注入时工具箱副本也要能被探测到（安装刚完成的场景）。"""
    box = get_config().bin_dir
    box.mkdir(parents=True)
    _fake_exe(box, "uv")
    assert detect("uv").source == "toolbox"


# ── 安装 ──


def _tar_with_binary(tmp_path, inner_path, content=b"#!/bin/sh\necho uv 0.11.32\n"):
    archive = tmp_path / "asset.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(inner_path)
        info.size = len(content)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(content))
    return archive


def test_install_extracts_binary(toolbox_env, tmp_path, monkeypatch):
    archive = _tar_with_binary(tmp_path, "uv-aarch64-apple-darwin/uv")

    def fake_download(url, dest, progress, phase):
        dest.write_bytes(archive.read_bytes())

    monkeypatch.setattr(toolbox, "_download", fake_download)
    monkeypatch.setattr(toolbox, "_plat", lambda: ("darwin", "arm64"))
    status = toolbox.install("uv")
    assert status.source == "toolbox"
    assert os.access(get_config().bin_dir / "uv", os.X_OK)


def test_install_node_tree_and_links(toolbox_env, tmp_path, monkeypatch):
    """node 解压整棵树（剥顶层目录）并 symlink node/npm/npx 进 bin。"""
    archive = tmp_path / "node.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for cmd in ("node", "npm", "npx"):
            content = f'#!/bin/sh\necho "{cmd} 24.18.0"\n'.encode()
            info = tarfile.TarInfo(f"node-v24.18.0-darwin-arm64/bin/{cmd}")
            info.size = len(content)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(content))

    def fake_download(url, dest, progress, phase):
        dest.write_bytes(archive.read_bytes())

    monkeypatch.setattr(toolbox, "_download", fake_download)
    monkeypatch.setattr(toolbox, "_plat", lambda: ("darwin", "arm64"))
    status = toolbox.install("node")
    assert status.source == "toolbox"
    assert (toolbox_env["config"] / "node" / "bin" / "node").exists()
    for cmd in ("node", "npm", "npx"):
        link = get_config().bin_dir / cmd
        assert link.is_symlink()


def test_install_missing_skips_present(toolbox_env, monkeypatch):
    """一键装齐只装 missing：system 项绝不重装。"""
    _fake_exe(toolbox_env["system_bin"], "rg")
    installed = []
    monkeypatch.setattr(
        toolbox, "install", lambda n, p=None: installed.append(n) or detect(n)
    )
    results = install_missing()
    assert set(installed) == {"uv", "node"}
    assert {s.name for s in results} == set(CORE_TOOLS)


def test_checksum_mismatch_rejects(toolbox_env, tmp_path, monkeypatch):
    monkeypatch.setattr(toolbox, "_fetch_checksum", lambda url: "0" * 64)

    class FakeResp(io.BytesIO):
        headers = {"Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        toolbox.urllib.request, "urlopen", lambda url, timeout=60: FakeResp(b"data")
    )
    dest = tmp_path / "out"
    with pytest.raises(RuntimeError, match="checksum"):
        toolbox._download("https://example.com/x.tar.gz", dest, None, "下载")
    assert not dest.exists()


@pytest.mark.parametrize(
    "text",
    [
        # uv / rg 的 POSIX 产物、node 的 SHASUMS256.txt 行
        "9f2c1c0f1e4d9b7a3c5e8f0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e  rg.tar.gz",
        # uv 的 Windows 产物（二进制标记 *）
        "9F2C1C0F1E4D9B7A3C5E8F0A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E *uv.zip",
        # rg 的 Windows 产物：CertUtil 排版，哈希在第二行
        "SHA256 hash of ripgrep-15.2.0-x86_64-pc-windows-msvc.zip:\r\n"
        "9f2c1c0f1e4d9b7a3c5e8f0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e\r\n"
        "CertUtil: -hashfile command completed successfully.\r\n",
    ],
)
def test_pick_sha256_across_publisher_formats(text):
    """三种官方排版都要挑得出哈希——按空白切首段会在 CertUtil 版上取到 "SHA256"。"""
    assert (
        toolbox._pick_sha256(text)
        == "9f2c1c0f1e4d9b7a3c5e8f0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e"
    )


# ── 飞书组件 ──

_EMBEDDED = {
    "skills": [
        {"name": "lark-im", "version": "1.2.0"},
        {"name": "lark-minutes", "version": "1.0.0"},
    ]
}


def _mock_lark_cli(monkeypatch, toolbox_env, files=None):
    """把 lark-cli 探测与子进程调用替换为 fixture 数据。"""
    _fake_exe(toolbox_env["system_bin"], "lark-cli", "1.0.77")
    files = files or {
        "lark-im": ["lark-im/SKILL.md"],
        "lark-minutes": ["lark-minutes/SKILL.md", "lark-minutes/references/api.md"],
    }

    def fake_run(cmd, timeout=30):
        if cmd[1:3] == ["skills", "list"] and len(cmd) == 3:
            return True, json.dumps(_EMBEDDED)
        if cmd[1:3] == ["skills", "list"]:  # 列某技能目录
            name = cmd[3]
            entries = [{"path": p, "is_dir": False} for p in files[name]]
            return True, json.dumps({"entries": entries})
        if cmd[1:3] == ["skills", "read"]:
            path = cmd[3]
            version = next(
                s["version"] for s in _EMBEDDED["skills"] if path.startswith(s["name"])
            )
            return (
                True,
                f"---\nname: {path.split('/')[0]}\nversion: {version}\n---\nbody",
            )
        return False, "unexpected"

    monkeypatch.setattr(toolbox, "_run", fake_run)


def test_sync_lark_skills_writes_all(toolbox_env, monkeypatch):
    _mock_lark_cli(monkeypatch, toolbox_env)
    assert sync_lark_skills() == 2
    skills = get_config().skills_dir
    assert (skills / "lark-im" / "SKILL.md").exists()
    assert (skills / "lark-minutes" / "references" / "api.md").exists()


def test_sync_lark_skills_incremental(toolbox_env, monkeypatch):
    """version 相同的技能跳过，不重写。"""
    _mock_lark_cli(monkeypatch, toolbox_env)
    sync_lark_skills()
    mtime = (get_config().skills_dir / "lark-im" / "SKILL.md").stat().st_mtime
    assert sync_lark_skills() == 0
    assert (get_config().skills_dir / "lark-im" / "SKILL.md").stat().st_mtime == mtime


def test_sync_without_cli_raises(toolbox_env):
    with pytest.raises(RuntimeError, match="lark-cli"):
        sync_lark_skills()


def test_skills_status_reports_outdated(toolbox_env, monkeypatch):
    _mock_lark_cli(monkeypatch, toolbox_env)
    sync_lark_skills()
    # 模拟 cli 升级后内嵌版本前进
    bumped = {"lark-im": "1.3.0", "lark-minutes": "1.0.0"}
    assert skills_status(bumped) == {"total": 2, "installed": 2, "outdated": 1}


def test_skills_status_partial_missing_counts_outdated(toolbox_env, monkeypatch):
    """cli 升级新增技能后，部分缺失必须计入 outdated——只看版本差会误报全绿。"""
    _mock_lark_cli(monkeypatch, toolbox_env)
    sync_lark_skills()
    grown = {"lark-im": "1.2.0", "lark-minutes": "1.0.0", "lark-new": "1.0.0"}
    assert skills_status(grown) == {"total": 3, "installed": 2, "outdated": 1}
    # 全新未装（installed=0）不把缺失算 outdated，仍走「未安装」语义
    assert skills_status(grown, str(toolbox_env["config"] / "nowhere")) == {
        "total": 3,
        "installed": 0,
        "outdated": 0,
    }


def test_lark_skill_versions_unreadable_returns_none(toolbox_env, monkeypatch):
    """清单读不到（旧版 cli / 非 JSON 输出）返回 None，与「0 个技能」严格区分。"""
    monkeypatch.setattr(
        toolbox, "_run", lambda cmd, timeout=30: (False, "unknown command")
    )
    assert lark_skill_versions("/usr/bin/lark-cli") is None
    monkeypatch.setattr(toolbox, "_run", lambda cmd, timeout=30: (True, "not json"))
    assert lark_skill_versions("/usr/bin/lark-cli") is None


def test_run_decodes_utf8_under_non_utf8_locale(monkeypatch):
    """中文 Windows 现场：locale 是 cp936，lark-cli 的中文 JSON 仍须解得出来。

    本文件唯一跑真子进程的用例——解码发生在 subprocess 内部，mock 掉就测不到。
    把 locale 强改成 gbk 复现客户机：不显式指定 UTF-8 时这里会解码失败，
    体检遂误报「lark-cli 不支持 skills 子命令，请先升级」。

    子进程直接写 UTF-8 字节、命令行全 ASCII（ascii() 转义），这样本用例在真 Windows
    上也成立——否则子进程的 print 会按当地 ANSI 代码页编码，测的就不是被测行为了。
    """
    import locale
    import sys

    monkeypatch.setattr(locale, "getencoding", lambda: "gbk")
    payload = (
        '{"ok": true, "skills": [{"name": "lark-approval", "version": "飞书审批"}]}'
    )
    code = f"import sys; sys.stdout.buffer.write({ascii(payload)}.encode('utf-8'))"
    ok, out = toolbox._run([sys.executable, "-c", code])
    assert ok
    assert json.loads(out)["skills"][0]["version"] == "飞书审批"


def test_sync_with_unreadable_manifest_raises(toolbox_env, monkeypatch):
    _fake_exe(toolbox_env["system_bin"], "lark-cli", "0.9.0")
    monkeypatch.setattr(
        toolbox, "_run", lambda cmd, timeout=30: (False, "unknown command")
    )
    with pytest.raises(RuntimeError, match="技能清单"):
        sync_lark_skills()


def test_sync_lark_skills_to_project_layer(toolbox_env, monkeypatch, tmp_path):
    """带 project_dir 时装到项目层 .lumi/skills，全局层不落文件。"""
    _mock_lark_cli(monkeypatch, toolbox_env)
    project = tmp_path / "myproj"
    assert sync_lark_skills(project_dir=str(project)) == 2
    assert (project / ".lumi" / "skills" / "lark-im" / "SKILL.md").exists()
    assert not (get_config().skills_dir / "lark-im").exists()
    # 按项目检测：项目层已装、全局层视角未装
    embedded = {"lark-im": "1.2.0", "lark-minutes": "1.0.0"}
    assert skills_status(embedded, str(project))["installed"] == 2
    assert skills_status(embedded)["installed"] == 0


def test_node_tool_path_platform_matrix(monkeypatch):
    """平台产物布局唯一权威：win 平铺且 npm/lark-cli 是 .cmd，posix 在 bin/ 下。"""
    from pathlib import Path

    root = Path("/x/node")
    monkeypatch.setattr(toolbox, "_plat", lambda: ("win", "x64"))
    assert toolbox._node_tool_path(root, "node") == root / "node.exe"
    assert toolbox._node_tool_path(root, "npm") == root / "npm.cmd"
    assert toolbox._node_tool_path(root, "lark-cli") == root / "lark-cli.cmd"
    monkeypatch.setattr(toolbox, "_plat", lambda: ("darwin", "arm64"))
    assert toolbox._node_tool_path(root, "npm") == root / "bin" / "npm"


def test_local_env_checks_states(toolbox_env, monkeypatch, tmp_path):
    """渠道体检本地环境组：cli 缺失 → 两项 error；已装未同步 → 技能包 error 带 fix_action。"""
    from lumi.gateway.channels.feishu.setup import local_env_checks

    checks = local_env_checks("")
    assert [c["key"] for c in checks] == ["cli", "skills"]
    assert checks[0]["tone"] == "error" and checks[0]["fix_action"] == "lark-cli"

    _mock_lark_cli(monkeypatch, toolbox_env)
    project = tmp_path / "p"
    checks = local_env_checks(str(project))
    assert checks[0]["tone"] == "ok"
    assert checks[1]["tone"] == "error" and checks[1]["fix_action"] == "feishu-skills"

    sync_lark_skills(project_dir=str(project))
    checks = local_env_checks(str(project))
    assert [c["tone"] for c in checks] == ["ok", "ok"]


# ── PATH 注入 ──


def test_inject_path_idempotent(toolbox_env):
    inject_path()
    inject_path()
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts.count(str(get_config().bin_dir)) == 1
    assert parts[-1] == str(get_config().bin_dir)  # 末尾追加，系统优先

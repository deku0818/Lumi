"""mcp_rpc：写前防抹除 + 路径 resolve 对齐 + save/delete 落盘与作废。"""

from __future__ import annotations

import stat

import pytest

from lumi.gateway import mcp_rpc


@pytest.fixture(autouse=True)
def _no_pool_side_effects(monkeypatch):
    """作废走真实 _pools 会读文件；测试里 stub 掉，只验 dispatch 自身逻辑。"""

    async def _noop(scope, project_dir=None):
        return None

    monkeypatch.setattr(mcp_rpc, "invalidate_mcp_pools", _noop)


async def test_save_then_list_roundtrip(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    await mcp_rpc.dispatch_mcp(
        "save_mcp_server",
        {
            "scope": "project",
            "project": str(proj),
            "name": "foo",
            "config": {"command": "npx", "transport": "stdio", "disabled": True},
        },
    )
    r = await mcp_rpc.dispatch_mcp(
        "list_mcp_servers", {"scope": "project", "project": str(proj)}
    )
    assert r["servers"]["foo"]["disabled"] is True  # 原始 dict 保留 disabled


async def test_corrupt_file_blocks_save_not_wipes(tmp_path):
    """存量文件损坏时 save 抛错，绝不用 {} 覆盖抹掉已有配置。"""
    path = tmp_path / "proj" / ".lumi" / "mcp_server.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ broken json,,,", encoding="utf-8")

    with pytest.raises(ValueError):
        await mcp_rpc.dispatch_mcp(
            "save_mcp_server",
            {
                "scope": "project",
                "project": str(tmp_path / "proj"),
                "name": "foo",
                "config": {"command": "x"},
            },
        )
    # 文件原样未动（没被覆盖成 {foo:...}）
    assert path.read_text(encoding="utf-8") == "{ broken json,,,"


async def test_corrupt_file_lists_empty(tmp_path):
    path = tmp_path / ".lumi" / "mcp_server.json"
    path.parent.mkdir(parents=True)
    path.write_text("nonsense", encoding="utf-8")
    # 全局 scope 但把 home 指向 tmp
    r = await mcp_rpc.dispatch_mcp(
        "list_mcp_servers", {"scope": "project", "project": str(tmp_path)}
    )
    assert r["servers"] == {}


async def test_saved_file_is_0600(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    await mcp_rpc.dispatch_mcp(
        "save_mcp_server",
        {
            "scope": "project",
            "project": str(proj),
            "name": "s",
            "config": {"url": "https://x"},
        },
    )
    path = proj / ".lumi" / "mcp_server.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # env/headers 可含密钥


async def test_project_path_resolved_matches_pool_key(tmp_path, monkeypatch):
    """RPC 作废用的 project_dir 与建池 key 口径一致（都 resolve），symlink 下不错位。"""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    captured = {}

    async def _capture(scope, project_dir=None):
        captured["dir"] = project_dir

    monkeypatch.setattr(mcp_rpc, "invalidate_mcp_pools", _capture)

    await mcp_rpc.dispatch_mcp(
        "save_mcp_server",
        {
            "scope": "project",
            "project": str(link),
            "name": "s",
            "config": {"command": "x"},
        },
    )
    # 作废传入的是 resolve 后的真实路径（= bridge 建池 key 口径）
    assert captured["dir"] == real.resolve()
    # 文件也写到 resolve 后的目录
    assert (real / ".lumi" / "mcp_server.json").exists()

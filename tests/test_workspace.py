"""workspace 路径授权与校验测试"""

import pytest

import lumi.agents.permissions.workspace as workspace
from lumi.agents.permissions.workspace import (
    add_authorized_directory,
    get_all_authorized_directories,
    get_authorized_directory,
    set_authorized_directory,
    set_run_authorized_source,
    validate_path,
)


def test_set_and_get_authorized_directory(tmp_path):
    set_authorized_directory(tmp_path)
    assert get_authorized_directory() == tmp_path.resolve()
    # teardown
    workspace._authorized_directories = []


def test_get_authorized_directory_default_cwd():
    workspace._authorized_directories = []
    from pathlib import Path

    assert get_authorized_directory() == Path.cwd().resolve()


def test_validate_path_relative_inside(authorized_tmp_dir):
    (authorized_tmp_dir / "sub").mkdir()
    (authorized_tmp_dir / "sub" / "file.txt").write_text("hi")
    result = validate_path("sub/file.txt")
    assert result == authorized_tmp_dir / "sub" / "file.txt"


def test_validate_path_absolute_inside(authorized_tmp_dir):
    f = authorized_tmp_dir / "ok.txt"
    f.write_text("ok")
    result = validate_path(str(f))
    assert result == f


def test_validate_path_traversal_rejected(authorized_tmp_dir):
    with pytest.raises(PermissionError):
        validate_path("../../etc/passwd")


def test_validate_path_symlink_traversal_rejected(authorized_tmp_dir):
    external_file = authorized_tmp_dir.parent / "external_secret.txt"
    external_file.write_text("secret")
    link = authorized_tmp_dir / "sneaky_link"
    link.symlink_to(external_file)
    with pytest.raises(PermissionError):
        validate_path("sneaky_link")
    # cleanup
    external_file.unlink()


def test_validate_path_dot_dot_normalization(authorized_tmp_dir):
    (authorized_tmp_dir / "subdir").mkdir()
    (authorized_tmp_dir / "other").mkdir()
    f = authorized_tmp_dir / "other" / "file.txt"
    f.write_text("data")
    result = validate_path("subdir/../other/file.txt")
    assert result == f


def test_add_authorized_directory(authorized_tmp_dir, tmp_path):
    """add_authorized_directory 应将额外目录加入授权列表。"""
    extra = tmp_path / "extra_dir"
    extra.mkdir()
    add_authorized_directory(extra)

    # 额外目录内的路径应通过校验
    f = extra / "test.txt"
    f.write_text("hello")
    result = validate_path(str(f))
    assert result == f.resolve()


def test_run_authorized_overrides_global(tmp_path):
    """回归（多会话 Path 2）：per-run 注入覆盖被别的会话清洗过的进程全局兜底。

    另一会话的引擎重建边界会 set_authorized_directory 把全局重置成只剩它的项目；
    本 run 仍应按自己注入的授权目录校验，看不到对方的目录，也不丢自己的临时目录。
    """
    other = (tmp_path / "other_session").resolve()
    mine_proj = (tmp_path / "mine_proj").resolve()
    mine_extra = (tmp_path / "mine_extra").resolve()
    for d in (other, mine_proj, mine_extra):
        d.mkdir()

    # 另一会话把进程全局重置成只有它的项目目录（teardown 由 autouse fixture 还原）
    set_authorized_directory(other)
    # 本 run 注入自己的授权目录来源（项目 + 临时），实时回调
    set_run_authorized_source(lambda: [mine_proj, mine_extra])

    dirs = get_all_authorized_directories()
    assert mine_proj in dirs and mine_extra in dirs  # 自己的目录在
    assert other not in dirs  # 对方清洗全局的影响被 run 覆盖层屏蔽
    assert get_authorized_directory() == mine_proj  # 主目录取本 run 首位

    (mine_extra / "f.txt").write_text("ok")
    assert validate_path(str(mine_extra / "f.txt")) == mine_extra / "f.txt"


def test_set_run_authorized_source_for_engine_vs_fallback(tmp_path):
    """共用 helper：有引擎用其实时回调，无引擎用 [cwd, *extra] 快照兜底。"""
    from pathlib import Path

    from lumi.agents.permissions.workspace import set_run_authorized_source_for

    proj = (tmp_path / "eng_proj").resolve()

    class _FakeEngine:
        def authorized_directories(self):
            return [proj]

    set_run_authorized_source_for(_FakeEngine())
    assert proj in get_all_authorized_directories()  # 有引擎 → 实时回调

    extra = (tmp_path / "extra").resolve()
    extra.mkdir()
    set_run_authorized_source_for(None, [str(extra)])  # 无引擎 → 快照兜底
    dirs = get_all_authorized_directories()
    assert extra in dirs and Path.cwd().resolve() in dirs


def test_system_info_cwd_follows_run_authorized(tmp_path):
    """项目随会话绑定：system_info 注入的 cwd 取 per-run 授权主目录，而非进程 os.getcwd()。"""
    from lumi.agents.core.preprocessing.system_info import collect_system_info

    proj = (tmp_path / "sess_proj").resolve()
    proj.mkdir()
    set_run_authorized_source(lambda: [proj])
    assert collect_system_info()["cwd"] == str(proj)


def test_validate_path_rejects_outside_all_dirs(authorized_tmp_dir):
    """不在任何授权目录内的路径应被拒绝。"""
    outside = authorized_tmp_dir.parent / "outside_test_dir"
    outside.mkdir(exist_ok=True)
    f = outside / "secret.txt"
    f.write_text("secret")
    try:
        with pytest.raises(PermissionError):
            validate_path(str(f))
    finally:
        f.unlink(missing_ok=True)
        outside.rmdir()

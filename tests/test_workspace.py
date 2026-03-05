"""workspace 路径授权与校验测试"""

import pytest

import lumi.agents.tools.workspace as workspace
from lumi.agents.tools.workspace import (
    add_authorized_directory,
    get_authorized_directory,
    set_authorized_directory,
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

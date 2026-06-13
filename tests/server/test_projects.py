"""项目清单存取测试（存储文件指向 tmp，纯文件断言）"""

import pytest

from lumi.server import projects


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "_PROJECTS_FILE", tmp_path / "projects.json")


def test_add_and_sort_by_recent(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    projects.add_project(str(tmp_path / "a"))
    result = projects.add_project(str(tmp_path / "b"))
    assert [p["name"] for p in result] == ["b", "a"]


def test_add_dedupes_and_touches(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    projects.add_project(str(tmp_path / "a"))
    projects.add_project(str(tmp_path / "b"))
    result = projects.add_project(str(tmp_path / "a"))
    assert len(result) == 2
    assert result[0]["name"] == "a"


def test_add_with_custom_name_and_rename_preserved(tmp_path):
    (tmp_path / "a").mkdir()
    result = projects.add_project(str(tmp_path / "a"), "我的项目")
    assert result[0]["name"] == "我的项目"
    # 重复添加不带名 → 保留自定义名（只刷新 last_used）
    result = projects.add_project(str(tmp_path / "a"))
    assert result[0]["name"] == "我的项目"


def test_add_missing_dir_raises(tmp_path):
    with pytest.raises(ValueError):
        projects.add_project(str(tmp_path / "nope"))


def test_remove_and_rename(tmp_path):
    (tmp_path / "a").mkdir()
    added = projects.add_project(str(tmp_path / "a"))
    path = added[0]["path"]
    renamed = projects.rename_project(path, "我的项目")
    assert renamed[0]["name"] == "我的项目"
    assert projects.remove_project(path) == []


def test_touch_unknown_path_ignored():
    projects.touch_project("/nonexistent")
    assert projects.list_projects() == []

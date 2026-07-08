from __future__ import annotations

from lumi.gateway import session as session_mod


def test_windows_drive_root_parent_is_virtual_root(monkeypatch):
    monkeypatch.setattr(session_mod.os, "name", "nt", raising=False)

    assert session_mod._parent_for_list_dir("C:\\") == session_mod._WINDOWS_ROOTS_PATH
    assert session_mod._parent_for_list_dir("D:/") == session_mod._WINDOWS_ROOTS_PATH
    assert session_mod._parent_for_list_dir("C:\\Users") == "C:\\"
    assert session_mod._parent_for_list_dir("\\\\server\\share\\") is None
    assert (
        session_mod._parent_for_list_dir("\\\\server\\share\\folder")
        == "\\\\server\\share\\"
    )


def test_posix_root_still_has_no_parent(monkeypatch):
    monkeypatch.setattr(session_mod.os, "name", "posix", raising=False)

    assert session_mod._parent_for_list_dir("/") is None


async def test_list_dir_windows_virtual_root(monkeypatch):
    monkeypatch.setattr(session_mod.os, "name", "nt", raising=False)
    monkeypatch.setattr(session_mod, "_windows_drive_roots", lambda: ["C:\\", "D:\\"])

    result = await session_mod._list_dir(
        None, {"path": session_mod._WINDOWS_ROOTS_PATH}
    )

    assert result == {
        "path": "",
        "parent": None,
        "dirs": ["C:\\", "D:\\"],
        "selectable": False,
    }

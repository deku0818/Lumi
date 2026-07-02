"""session_meta sidecar：变更检测与删除后重建。"""

from lumi.sessions import session_meta


def test_update_meta_skips_write_when_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(session_meta, "_meta_path", lambda: tmp_path / "meta.json")
    session_meta.update_meta("t1", channel_title="Lumi 内测群", channel_kind="group")
    mtime = (tmp_path / "meta.json").stat().st_mtime_ns

    # 同值再写：内容一致不落盘（飞书每条消息都调，靠此免高频写）
    session_meta.update_meta("t1", channel_title="Lumi 内测群", channel_kind="group")
    assert (tmp_path / "meta.json").stat().st_mtime_ns == mtime


def test_update_meta_rewrites_after_delete(tmp_path, monkeypatch):
    # 「清空记忆」删掉条目后，下一次同值 update 必须能重建（无内存缓存可失效）
    monkeypatch.setattr(session_meta, "_meta_path", lambda: tmp_path / "meta.json")
    session_meta.update_meta("t1", channel_title="Lumi 内测群")
    session_meta.delete_meta("t1")
    session_meta.update_meta("t1", channel_title="Lumi 内测群")
    assert session_meta.load_all()["t1"]["channel_title"] == "Lumi 内测群"

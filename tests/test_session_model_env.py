"""纯 env 配置（没有任何 provider profile）下的会话模型。"""

from __future__ import annotations

from lumi.models import provider_store
from lumi.sessions import session_meta, session_model


def test_pin_without_connection_is_noop(monkeypatch, tmp_path):
    # 回归：没有连接可固化，每轮 pin 都写 {'model': 'gpt-5'}，下一次 resolve 又把它当失效
    # 清掉并记一条 WARNING——永远固化不上，日志每轮刷一条
    monkeypatch.setattr(session_meta, "_meta_path", lambda: tmp_path / "meta.json")
    monkeypatch.setattr(
        provider_store,
        "resolve",
        lambda *a, **k: provider_store.ResolvedModel("gpt-5", "", ""),
    )
    assert session_model.pin("t1").pinned is False
    assert "model" not in session_meta.load_all().get("t1", {})

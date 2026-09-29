"""锁住用到的 AsyncPostgresSaver 私有接口（不需要真 PG）。

会话列表的 PG 快路径（``sessions/session_store.py``）直接拼 ``_search_where`` 的
WHERE 子句，删线程（``agents/core/graph.py``）用 ``_cursor(pipeline=True)``。上游改了
签名的话只会在 PG 部署的运行时炸，本地 sqlite 跑全绿——这里在升级依赖时先报出来。
"""

import inspect

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


def test_search_where_signature():
    params = list(inspect.signature(AsyncPostgresSaver._search_where).parameters)
    assert params[:3] == ["self", "config", "filter"]


def test_cursor_accepts_pipeline():
    assert "pipeline" in inspect.signature(AsyncPostgresSaver._cursor).parameters

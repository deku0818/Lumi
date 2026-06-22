"""WS 鉴权 token 校验（纯函数断言）。"""

from lumi.gateway.channels.ws import token_ok


def test_no_config_allows_all():
    # 未配置 token（本地默认/旧行为）：任何携带都放行
    assert token_ok("", None)
    assert token_ok("", "anything")


def test_configured_requires_exact_match():
    assert token_ok("secret", "secret")


def test_configured_rejects_wrong():
    assert not token_ok("secret", "wrong")


def test_configured_rejects_missing():
    assert not token_ok("secret", None)

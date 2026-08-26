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


def test_non_ascii_token_matches():
    # 令牌是用户自起的，写成中文完全合法；compare_digest 收非 ASCII 的 str 会抛
    # TypeError，那会让每次连接崩在鉴权行上（而非干净拒绝）
    assert token_ok("我的口令", "我的口令")


def test_non_ascii_token_rejects_wrong():
    assert not token_ok("我的口令", "别人的口令")
    assert not token_ok("我的口令", "ascii-guess")
    assert not token_ok("ascii-secret", "中文猜测")

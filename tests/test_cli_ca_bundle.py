"""CA bundle 兜底：PyInstaller 冻结产物的 OpenSSL 默认路径失效时回退 certifi。"""

import os
import ssl
from unittest.mock import patch

from lumi.cli import _ensure_ca_bundle


def _paths(cafile: str | None) -> ssl.DefaultVerifyPaths:
    return ssl.DefaultVerifyPaths(None, None, None, cafile, None, None)


def test_keeps_env_when_default_ca_exists(monkeypatch):
    """dev / 容器：默认路径真实存在，一律不动（避免覆盖系统信任库）。"""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    with patch("ssl.get_default_verify_paths", return_value=_paths(__file__)):
        _ensure_ca_bundle()
    assert "SSL_CERT_FILE" not in os.environ


def test_falls_back_to_certifi_when_default_ca_missing(monkeypatch):
    """冻结产物：路径指向构建机、本机不存在 → 回退 certifi。

    不兜底则 ssl 默认上下文加载到 0 张 CA，任何证书链都被判成自签不可信；HTTP 侧因
    显式用 certifi 而正常，故表现为「只有飞书 WS 一直连接中」这种极具迷惑性的局部失效。
    """
    import certifi

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    with patch("ssl.get_default_verify_paths", return_value=_paths("/nonexistent/ci")):
        _ensure_ca_bundle()
    assert os.environ["SSL_CERT_FILE"] == certifi.where()


def test_respects_explicit_override(monkeypatch):
    """用户显式指定（如企业内网自签 CA）优先于兜底。"""
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")
    with patch("ssl.get_default_verify_paths", return_value=_paths("/nonexistent/ci")):
        _ensure_ca_bundle()
    assert os.environ["SSL_CERT_FILE"] == "/custom/ca.pem"

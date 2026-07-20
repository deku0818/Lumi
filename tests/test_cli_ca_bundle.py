"""CA bundle 兜底：PyInstaller 冻结产物的 OpenSSL 默认路径失效时回退 certifi。"""

import os
import ssl
from unittest.mock import patch

import pytest

from lumi.cli import _ensure_ca_bundle


def _paths(cafile: str | None, capath: str | None = None) -> ssl.DefaultVerifyPaths:
    return ssl.DefaultVerifyPaths(None, None, None, cafile, None, capath)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """整份 os.environ 换成副本再跑。

    _ensure_ca_bundle 是直接写 os.environ 的，而 monkeypatch.delenv 对「原本不存在」的
    变量不做记录、也就无从回滚——certifi 路径会泄漏给同会话的后续测试，让依赖系统默认
    CA 的用例随执行顺序漂移。
    """
    monkeypatch.setattr(os, "environ", dict(os.environ))
    os.environ.pop("SSL_CERT_FILE", None)


def test_keeps_env_when_default_ca_exists():
    """dev / 容器：默认路径真实存在，一律不动（避免覆盖系统信任库）。"""
    with patch("ssl.get_default_verify_paths", return_value=_paths(__file__)):
        _ensure_ca_bundle()
    assert "SSL_CERT_FILE" not in os.environ


def test_keeps_env_when_only_capath_exists():
    """仅靠 capath 建立信任的系统：同样不得覆盖。

    cafile 不存在而 capath 目录真实（且可能已被 update-ca-certificates 灌入企业自签
    CA）时若改判 certifi，内网 HTTPS 端点会突然不可信。
    """
    with patch(
        "ssl.get_default_verify_paths",
        return_value=_paths("/nonexistent/ci", os.path.dirname(__file__)),
    ):
        _ensure_ca_bundle()
    assert "SSL_CERT_FILE" not in os.environ


def test_falls_back_to_certifi_when_default_ca_missing():
    """冻结产物：路径指向构建机、本机不存在 → 回退 certifi。

    不兜底则 ssl 默认上下文加载到 0 张 CA，任何证书链都被判成自签不可信；HTTP 侧因
    显式用 certifi 而正常，故表现为「只有飞书 WS 一直连接中」这种极具迷惑性的局部失效。
    """
    import certifi

    with patch(
        "ssl.get_default_verify_paths",
        return_value=_paths("/nonexistent/ci", "/nonexistent/certs"),
    ):
        _ensure_ca_bundle()
    assert os.environ["SSL_CERT_FILE"] == certifi.where()


def test_respects_explicit_override(monkeypatch):
    """用户显式指定（如企业内网自签 CA）优先于兜底。"""
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")
    with patch("ssl.get_default_verify_paths", return_value=_paths("/nonexistent/ci")):
        _ensure_ca_bundle()
    assert os.environ["SSL_CERT_FILE"] == "/custom/ca.pem"

from unittest.mock import patch

import pytest

from app.core import vless as vless_module


@pytest.fixture
def tcp_reality_env():
    with patch.multiple(
        vless_module,
        VLESS_TYPE="tcp",
        VLESS_SECURITY="reality",
        VLESS_PBK="test_public_key",
        VLESS_FP="chrome",
        VLESS_SNI="www.microsoft.com",
        VLESS_SID="a1b2c3d4",
        VLESS_SPX="/",
        VLESS_XHTTP_PATH="/",
        VLESS_XHTTP_HOST="",
        VLESS_XHTTP_MODE="auto",
        SERVER_IP="1.2.3.4",
        VLESS_PORT="443",
        VLESS_USE_FRAGMENT=False,
    ):
        yield


@pytest.fixture
def xhttp_reality_env():
    with patch.multiple(
        vless_module,
        VLESS_TYPE="xhttp",
        VLESS_SECURITY="reality",
        VLESS_PBK="test_public_key",
        VLESS_FP="chrome",
        VLESS_SNI="www.microsoft.com",
        VLESS_SID="a1b2c3d4",
        VLESS_SPX="/",
        VLESS_XHTTP_PATH="/xhttp",
        VLESS_XHTTP_HOST="www.microsoft.com",
        VLESS_XHTTP_MODE="stream-one",
        SERVER_IP="1.2.3.4",
        VLESS_PORT="443",
        VLESS_USE_FRAGMENT=False,
    ):
        yield


def test_build_tcp_reality_link(tcp_reality_env):
    link = vless_module.build_vless_link(
        "e78fb305-b0c7-4378-9b4d-79dce7ee162d",
        "alice",
    )
    assert link.startswith("vless://e78fb305-b0c7-4378-9b4d-79dce7ee162d@1.2.3.4:443?")
    assert "type=tcp" in link
    assert "security=reality" in link
    assert "pbk=test_public_key" in link
    assert "sni=www.microsoft.com" in link
    assert "sid=a1b2c3d4" in link
    assert "spx=%2F" in link
    assert "path=" not in link
    assert "mode=" not in link
    assert "#" not in link
    assert link.endswith("spx=%2F")


def test_build_xhttp_reality_link(xhttp_reality_env):
    link = vless_module.build_vless_link(
        "e78fb305-b0c7-4378-9b4d-79dce7ee162d",
        "bob",
    )
    assert "type=xhttp" in link
    assert "path=%2Fxhttp" in link
    assert "host=www.microsoft.com" in link
    assert "mode=stream-one" in link
    assert "flow=" not in link
    assert "security=reality" in link
    assert "pbk=test_public_key" in link
    assert "#" not in link
    assert link.endswith("spx=%2F")


def test_build_xhttp_empty_host_like_panel(xhttp_reality_env):
    with patch.object(vless_module, "VLESS_XHTTP_HOST", ""):
        link = vless_module.build_vless_link("uuid", "alice")
    assert "host=&" in link or "host=" in link.split("mode=")[0]


def test_fragment_optional_for_legacy_clients(xhttp_reality_env):
    with patch.object(vless_module, "VLESS_USE_FRAGMENT", True):
        link = vless_module.build_vless_link("uuid", "alice")
    assert link.endswith("#METRON_alice")
    assert "spx=%2F#" not in link

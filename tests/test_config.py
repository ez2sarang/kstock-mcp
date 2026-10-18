import pytest

from kstock_mcp.config import Settings
from kstock_mcp.errors import KStockError


def test_defaults_to_paper_and_never_prints_secrets():
    s = Settings.from_env({"KIS_APP_KEY": "k-123", "KIS_APP_SECRET": "s-456"})
    assert s.kis_env == "paper"
    assert "openapivts" in s.kis_base_url
    assert "k-123" not in repr(s) and "s-456" not in repr(s)


@pytest.mark.parametrize("raw,expected", [("real", "real"), ("PROD", "real"), ("paper", "paper"), ("demo", "paper"), ("", "paper")])
def test_env_names(raw, expected):
    assert Settings.from_env({"KIS_ENV": raw}).kis_env == expected


def test_unknown_env_is_rejected_not_silently_real():
    with pytest.raises(KStockError):
        Settings.from_env({"KIS_ENV": "staging"})


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"KIS_ACCOUNT_NO": "12345678-01"}, ("12345678", "01")),
        ({"KIS_ACCOUNT_NO": "1234567803"}, ("12345678", "03")),
        ({"KIS_ACCOUNT_NO": "12345678"}, ("12345678", "01")),
        ({"KIS_CANO": "12345678", "KIS_ACNT_PRDT_CD": "22"}, ("12345678", "22")),
        ({"KIS_CANO": "12345678"}, ("12345678", "01")),
        ({}, ("", "")),
    ],
)
def test_account_parsing(env, expected):
    s = Settings.from_env(env)
    assert (s.kis_cano, s.kis_acnt_prdt_cd) == expected


def test_bad_account_rejected():
    with pytest.raises(KStockError):
        Settings.from_env({"KIS_ACCOUNT_NO": "123"})


def test_require_helpers(tmp_path):
    s = Settings.from_env({"KSTOCK_MCP_CACHE_DIR": str(tmp_path)})
    assert s.cache_dir == tmp_path
    with pytest.raises(KStockError, match="KIS_APP_KEY"):
        s.require_kis()
    with pytest.raises(KStockError, match="DART_API_KEY"):
        s.require_dart()

"""Environment-based settings. Secrets come from the environment only and are never logged."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import KStockError

KIS_BASE_URLS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "paper": "https://openapivts.koreainvestment.com:29443",
}
_PAPER_ALIASES = {"paper", "demo", "vts", "mock", "virtual"}
_REAL_ALIASES = {"real", "prod", "live"}


@dataclass(frozen=True)
class Settings:
    kis_env: str = "paper"
    kis_app_key: str = field(default="", repr=False)
    kis_app_secret: str = field(default="", repr=False)
    kis_cano: str = field(default="", repr=False)
    kis_acnt_prdt_cd: str = field(default="", repr=False)
    dart_api_key: str = field(default="", repr=False)
    cache_dir: Path = Path.home() / ".cache" / "kstock-mcp"

    @property
    def kis_base_url(self) -> str:
        return KIS_BASE_URLS[self.kis_env]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        cano, prdt = _parse_account(env)
        return cls(
            kis_env=_parse_env_name(env.get("KIS_ENV", "")),
            kis_app_key=env.get("KIS_APP_KEY", "").strip(),
            kis_app_secret=env.get("KIS_APP_SECRET", "").strip(),
            kis_cano=cano,
            kis_acnt_prdt_cd=prdt,
            dart_api_key=env.get("DART_API_KEY", "").strip(),
            cache_dir=_cache_dir(env),
        )

    def require_kis(self, *, account: bool = False) -> None:
        missing = [n for n, v in (("KIS_APP_KEY", self.kis_app_key), ("KIS_APP_SECRET", self.kis_app_secret)) if not v]
        if account and not self.kis_cano:
            missing.append("KIS_ACCOUNT_NO")
        if missing:
            raise KStockError(f"Missing environment variable(s): {', '.join(missing)}. See the README for setup.")

    def require_dart(self) -> None:
        if not self.dart_api_key:
            raise KStockError("Missing environment variable: DART_API_KEY (free key from https://opendart.fss.or.kr).")


def _parse_env_name(raw: str) -> str:
    name = raw.strip().lower()
    if not name:
        return "paper"
    if name in _PAPER_ALIASES:
        return "paper"
    if name in _REAL_ALIASES:
        return "real"
    raise KStockError("KIS_ENV must be 'paper' (default) or 'real'.")


def _parse_account(env: Mapping[str, str]) -> tuple[str, str]:
    """Accept KIS_ACCOUNT_NO ('12345678-01' / '1234567801' / '12345678') or KIS_CANO + KIS_ACNT_PRDT_CD."""
    raw = env.get("KIS_ACCOUNT_NO", "").strip()
    if raw:
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 10:
            return digits[:8], digits[8:]
        if len(digits) == 8:
            return digits, "01"
        raise KStockError("KIS_ACCOUNT_NO must be 8 digits, or 10 digits / '12345678-01'.")
    cano = re.sub(r"\D", "", env.get("KIS_CANO", ""))
    if not cano:
        return "", ""
    if len(cano) != 8:
        raise KStockError("KIS_CANO must be the first 8 digits of the account number.")
    prdt = re.sub(r"\D", "", env.get("KIS_ACNT_PRDT_CD", "")) or "01"
    return cano, prdt


def _cache_dir(env: Mapping[str, str]) -> Path:
    if env.get("KSTOCK_MCP_CACHE_DIR"):
        return Path(env["KSTOCK_MCP_CACHE_DIR"]).expanduser()
    base = env.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "kstock-mcp"

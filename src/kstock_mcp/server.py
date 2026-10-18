"""MCP server entry point: seven read-only tools for the Korean stock market (stdio)."""

from __future__ import annotations

import functools
import logging
import sys
from typing import Annotated, Any

import httpx2
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .config import Settings
from .dart_client import DartClient
from .errors import KStockError
from .ipo import build_ipo_schedule
from .kis_client import KISClient
from .stock_master import StockMaster

NOTICE = "Informational data only, not investment advice. Investment decisions are your own responsibility."

INSTRUCTIONS = (
    "K-Stock MCP gives read-only access to Korean stock market data: KIS Open API (quotes, OHLCV, "
    "account balance), OpenDART (disclosures, financials, IPO filings). It provides data and "
    "calculations only: it does not recommend buying or selling and it cannot place orders. "
    "Do not present tool output as investment advice; investment decisions and their results "
    "are the user's own responsibility. KIS access defaults to the paper-trading environment. "
    "Use search_stock first to turn a company name into a 6-digit code."
)

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

# OpenDART passes its API key as a query parameter and httpx2 logs full URLs at INFO, so HTTP
# client logging is capped at WARNING for every entry path (console script, python -m, MCPB launcher).
for _name in ("httpx2", "httpcore2", "httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

mcp = MCPServer("kstock-mcp", instructions=INSTRUCTIONS, version=__version__, log_level="WARNING")


class App:
    """Lazily built service container (so the server starts and lists tools without credentials)."""

    def __init__(self, settings: Settings | None = None, http: httpx2.Client | None = None):
        self.settings = settings or Settings.from_env()
        self.http = http or httpx2.Client(
            timeout=httpx2.Timeout(15.0), headers={"User-Agent": f"kstock-mcp/{__version__}"}
        )
        self.kis = KISClient(self.settings, self.http)
        self.dart = DartClient(self.settings, self.http)
        self.master = StockMaster(self.settings, self.http)


_app: App | None = None


def get_app() -> App:
    global _app
    if _app is None:
        _app = App()
    return _app


def _tool(fn):
    """Convert KStockError into a model-visible tool error and attach the short notice."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> dict[str, Any]:
        try:
            result = fn(*args, **kwargs)
        except KStockError as exc:
            raise ToolError(str(exc)) from None
        result["notice"] = NOTICE
        return result

    return mcp.tool(annotations=_READ_ONLY)(wrapper)


@_tool
def search_stock(
    query: Annotated[str, Field(description="Company name (Korean) or stock code, e.g. '삼성전자' or '005930'")],
    limit: Annotated[int, Field(description="Maximum results (1-50)", ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Find Korean listed securities (KOSPI/KOSDAQ/KONEX) by name or code. Returns code, name, market, type."""
    return get_app().master.search(query, limit)


@_tool
def get_quote(
    code: Annotated[str, Field(description="6-character stock code, e.g. '005930'")],
    include_orderbook: Annotated[bool, Field(description="Also fetch the 10-level order book (one extra KIS call)")] = True,
) -> dict[str, Any]:
    """Current price, day range, volume, valuation ratios and order book for a stock (KIS, KRX market)."""
    return get_app().kis.get_quote(code, include_orderbook)


@_tool
def get_ohlcv(
    code: Annotated[str, Field(description="6-character stock code, e.g. '005930'")],
    period: Annotated[str, Field(description="D daily, W weekly, M monthly, Y yearly")] = "D",
    start_date: Annotated[str | None, Field(description="YYYYMMDD or YYYY-MM-DD (default: ~120 days back for daily)")] = None,
    end_date: Annotated[str | None, Field(description="YYYYMMDD or YYYY-MM-DD (default: today, KST)")] = None,
    adjusted: Annotated[bool, Field(description="Use split/dividend-adjusted prices")] = True,
) -> dict[str, Any]:
    """Historical candles (open/high/low/close/volume), oldest first. Up to ~300 rows per call; see 'truncated'."""
    return get_app().kis.get_ohlcv(code, period, start_date, end_date, adjusted)


@_tool
def get_balance() -> dict[str, Any]:
    """Holdings and account summary for the configured KIS account (paper by default). Read-only."""
    return get_app().kis.get_balance()


@_tool
def get_ipo_schedule(
    days_back: Annotated[int, Field(description="How many days of filings to scan (1-90)", ge=1, le=90)] = 45,
    limit: Annotated[int, Field(description="Maximum number of IPO candidates (1-30)", ge=1, le=30)] = 10,
    expected_subscribers: Annotated[
        int | None,
        Field(description="If set, adds an equal-allocation (균등배정) estimate per IPO using this subscriber count", ge=1),
    ] = None,
    general_ratio_pct: Annotated[float, Field(description="General-subscriber share of the offering (%)", ge=0, le=100)] = 25.0,
    equal_ratio_pct: Annotated[float, Field(description="Equal-allocation share of the general-subscriber shares (%)", ge=0, le=100)] = 50.0,
    min_subscription_shares: Annotated[int, Field(description="Minimum subscription lot (shares)", ge=1)] = 10,
    deposit_rate_pct: Annotated[float, Field(description="Subscription deposit rate (%)", ge=0, le=100)] = 50.0,
) -> dict[str, Any]:
    """Upcoming/ongoing IPO (공모주) schedule from OpenDART equity filings: subscription dates, price text,
    underwriters, and an optional equal-allocation estimate whose formula and assumptions are in the response."""
    return build_ipo_schedule(
        get_app().dart, days_back, limit,
        expected_subscribers=expected_subscribers, general_ratio_pct=general_ratio_pct,
        equal_ratio_pct=equal_ratio_pct, min_subscription_shares=min_subscription_shares,
        deposit_rate_pct=deposit_rate_pct,
    )


@_tool
def get_disclosure(
    symbol: Annotated[str, Field(description="6-character stock code (preferred) or exact company name")],
    days: Annotated[int, Field(description="Look-back window in days", ge=1, le=3650)] = 90,
    kind: Annotated[
        str | None,
        Field(description="Optional type: A periodic, B major matters, C issuance, D ownership, E other, F audit, I exchange"),
    ] = None,
    limit: Annotated[int, Field(description="Maximum filings (1-100)", ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Recent DART disclosures for a company, newest first, with links to the DART viewer."""
    return get_app().dart.get_disclosures(symbol, days, kind, limit)


@_tool
def get_financials(
    symbol: Annotated[str, Field(description="6-character stock code (preferred) or exact company name")],
    year: Annotated[int | None, Field(description="Fiscal year (2015+). Default: last year", ge=2015)] = None,
    report: Annotated[str, Field(description="annual, q1, half, or q3")] = "annual",
    consolidated: Annotated[bool, Field(description="Prefer consolidated statements (falls back to separate)")] = True,
) -> dict[str, Any]:
    """Key financial statement accounts (assets, liabilities, equity, revenue, operating income, net income) from DART."""
    return get_app().dart.get_financials(symbol, year, report, consolidated)


def main() -> None:
    if "--version" in sys.argv[1:]:
        sys.stdout.write(f"kstock-mcp {__version__}\n")
        return
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)  # stdout carries the MCP protocol
    mcp.run()


if __name__ == "__main__":
    main()

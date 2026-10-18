"""Read-only KIS Open API client: quote/orderbook, OHLCV, balance.

Endpoints, TR ids and parameters follow the official examples at
https://github.com/koreainvestment/open-trading-api (examples_llm/domestic_stock/*).
No order endpoints are implemented.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import httpx2

from .config import Settings
from .errors import KStockError, network_error
from .kis_auth import KST, KISTokenManager

# Official minimum spacing between calls (kis_auth.py: 0.05s real, 0.5s paper).
_MIN_INTERVAL = {"real": 0.05, "paper": 0.5}
_TOKEN_ERRORS = {"EGW00121", "EGW00123"}  # invalid / expired access token
_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
_PERIODS = {"D": "D", "DAY": "D", "DAILY": "D", "W": "W", "WEEK": "W", "WEEKLY": "W",
            "M": "M", "MONTH": "M", "MONTHLY": "M", "Y": "Y", "YEAR": "Y", "YEARLY": "Y"}
_DEFAULT_LOOKBACK_DAYS = {"D": 120, "W": 730, "M": 1825, "Y": 3650}
_PAGE_ROWS = 100  # KIS returns at most 100 rows per call for the period chart


def normalize_code(code: str) -> str:
    value = (code or "").strip().upper()
    if not _CODE_RE.match(value):
        raise KStockError("Stock code must be 6 characters (e.g. 005930). Use search_stock to find a code.")
    return value


def parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    digits = re.sub(r"\D", "", value)
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        raise KStockError(f"Invalid date '{value}'. Use YYYYMMDD or YYYY-MM-DD.") from None


def to_int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def to_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def today_kst() -> date:
    return datetime.now(KST).date()


class KISClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx2.Client,
        tokens: KISTokenManager | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._s = settings
        self._http = http
        self._tokens = tokens or KISTokenManager(settings, http)
        self._sleep = sleep
        self._last_call = 0.0
        self._throttle_lock = threading.Lock()

    # ------------------------------------------------------------------ transport
    def _throttle(self) -> None:
        with self._throttle_lock:
            wait = _MIN_INTERVAL[self._s.kis_env] - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()

    def _get(self, path: str, tr_id: str, params: dict[str, str], tr_cont: str = "") -> tuple[dict, str]:
        """GET a KIS endpoint. Returns (json body, response tr_cont header)."""
        self._s.require_kis()
        for attempt in (1, 2):
            self._throttle()
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self._tokens.get_token()}",
                "appkey": self._s.kis_app_key,
                "appsecret": self._s.kis_app_secret,
                "tr_id": tr_id,
                "custtype": "P",
                "tr_cont": tr_cont,
            }
            try:
                resp = self._http.get(f"{self._s.kis_base_url}{path}", headers=headers, params=params)
            except httpx2.HTTPError as exc:
                raise network_error("KIS", exc) from None
            try:
                body = resp.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                raise KStockError(f"KIS returned an unexpected response (HTTP {resp.status_code}).")
            msg_cd = str(body.get("msg_cd", ""))
            if msg_cd in _TOKEN_ERRORS and attempt == 1:
                self._tokens.invalidate()
                continue
            if resp.status_code != 200 or str(body.get("rt_cd", "0")) != "0":
                raise KStockError(f"KIS error [{msg_cd or resp.status_code}] {body.get('msg1', '')}".strip())
            return body, str(resp.headers.get("tr_cont", "")).strip()
        raise KStockError("KIS access token could not be refreshed.")  # pragma: no cover

    # ------------------------------------------------------------------ tools
    def get_quote(self, code: str, include_orderbook: bool = True) -> dict[str, Any]:
        code = normalize_code(code)
        body, _ = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        o = body.get("output") or {}
        if not o:
            raise KStockError(f"No quote returned for {code}. Check the code with search_stock.")
        quote: dict[str, Any] = {
            "code": code,
            "market": o.get("rprs_mrkt_kor_name", ""),
            "price": to_int(o.get("stck_prpr")),
            "change": to_int(o.get("prdy_vrss")),
            "change_sign": o.get("prdy_vrss_sign", ""),  # 1 upper limit, 2 up, 3 flat, 4 lower limit, 5 down
            "change_rate_pct": to_float(o.get("prdy_ctrt")),
            "open": to_int(o.get("stck_oprc")),
            "high": to_int(o.get("stck_hgpr")),
            "low": to_int(o.get("stck_lwpr")),
            "prev_close": to_int(o.get("stck_sdpr")),
            "upper_limit": to_int(o.get("stck_mxpr")),
            "lower_limit": to_int(o.get("stck_llam")),
            "volume": to_int(o.get("acml_vol")),
            "trade_value": to_int(o.get("acml_tr_pbmn")),
            "week52_high": to_int(o.get("w52_hgpr")),
            "week52_low": to_int(o.get("w52_lwpr")),
            "market_cap_100m_krw": to_int(o.get("hts_avls")),
            "listed_shares": to_int(o.get("lstn_stcn")),
            "per": to_float(o.get("per")),
            "pbr": to_float(o.get("pbr")),
            "eps": to_float(o.get("eps")),
            "bps": to_float(o.get("bps")),
            "currency": "KRW",
            "environment": self._s.kis_env,
        }
        if include_orderbook:
            quote["orderbook"] = self._orderbook(code)
        return quote

    def _orderbook(self, code: str) -> dict[str, Any]:
        body, _ = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            "FHKST01010200",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        o = body.get("output1") or {}
        asks = [{"price": to_int(o.get(f"askp{i}")), "qty": to_int(o.get(f"askp_rsqn{i}"))} for i in range(1, 11)]
        bids = [{"price": to_int(o.get(f"bidp{i}")), "qty": to_int(o.get(f"bidp_rsqn{i}"))} for i in range(1, 11)]
        return {
            "asks": [lvl for lvl in asks if lvl["price"] > 0],  # best (lowest) ask first
            "bids": [lvl for lvl in bids if lvl["price"] > 0],  # best (highest) bid first
            "total_ask_qty": to_int(o.get("total_askp_rsqn")),
            "total_bid_qty": to_int(o.get("total_bidp_rsqn")),
            "time": o.get("aspr_acpt_hour", ""),
        }

    def get_ohlcv(
        self,
        code: str,
        period: str = "D",
        start_date: str | None = None,
        end_date: str | None = None,
        adjusted: bool = True,
        max_pages: int = 3,
    ) -> dict[str, Any]:
        code = normalize_code(code)
        div = _PERIODS.get((period or "").strip().upper())
        if not div:
            raise KStockError("period must be one of D (daily), W (weekly), M (monthly), Y (yearly).")
        end = parse_date(end_date, today_kst())
        start = parse_date(start_date, end - timedelta(days=_DEFAULT_LOOKBACK_DAYS[div]))
        if start > end:
            raise KStockError("start_date must be on or before end_date.")

        rows: dict[str, dict[str, Any]] = {}
        name = ""
        cursor_end = end
        truncated = False
        for page in range(max(1, max_pages)):
            body, _ = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": cursor_end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": div,
                    "FID_ORG_ADJ_PRC": "0" if adjusted else "1",  # 0 adjusted, 1 raw (official)
                },
            )
            name = name or (body.get("output1") or {}).get("hts_kor_isnm", "")
            batch = [r for r in (body.get("output2") or []) if r.get("stck_bsop_date")]
            for r in batch:
                d = datetime.strptime(r["stck_bsop_date"], "%Y%m%d").date()
                if d < start:
                    continue
                rows[d.isoformat()] = {
                    "date": d.isoformat(),
                    "open": to_int(r.get("stck_oprc")),
                    "high": to_int(r.get("stck_hgpr")),
                    "low": to_int(r.get("stck_lwpr")),
                    "close": to_int(r.get("stck_clpr")),
                    "volume": to_int(r.get("acml_vol")),
                    "trade_value": to_int(r.get("acml_tr_pbmn")),
                }
            if len(batch) < _PAGE_ROWS:
                break
            earliest = min(datetime.strptime(r["stck_bsop_date"], "%Y%m%d").date() for r in batch)
            if earliest <= start:
                break
            if page == max_pages - 1:
                truncated = True
                break
            cursor_end = earliest - timedelta(days=1)
        return {
            "code": code,
            "name": name,
            "period": div,
            "adjusted": adjusted,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "count": len(rows),
            "truncated": truncated,  # True: older rows exist beyond the page cap; narrow start_date or page the range
            "candles": [rows[k] for k in sorted(rows)],
            "environment": self._s.kis_env,
        }

    def get_balance(self) -> dict[str, Any]:
        self._s.require_kis(account=True)
        env = self._s.kis_env
        tr_id = "TTTC8434R" if env == "real" else "VTTC8434R"
        raw: list[dict] = []
        summary: dict = {}
        fk100 = nk100 = ""
        tr_cont = ""
        for _ in range(10):  # official example caps continuation depth at 10
            body, more = self._get(
                "/uapi/domestic-stock/v1/trading/inquire-balance",
                tr_id,
                {
                    "CANO": self._s.kis_cano,
                    "ACNT_PRDT_CD": self._s.kis_acnt_prdt_cd,
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": "",
                    "INQR_DVSN": "02",  # by symbol
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "00",  # include previous-day trades
                    "CTX_AREA_FK100": fk100,
                    "CTX_AREA_NK100": nk100,
                },
                tr_cont=tr_cont,
            )
            raw.extend(body.get("output1") or [])
            if body.get("output2"):
                summary = body["output2"][0]
            if more not in ("M", "F"):
                break
            fk100 = str(body.get("ctx_area_fk100", "")).strip()
            nk100 = str(body.get("ctx_area_nk100", "")).strip()
            tr_cont = "N"
        holdings = [
            {
                "code": h.get("pdno", ""),
                "name": h.get("prdt_name", ""),
                "quantity": to_int(h.get("hldg_qty")),
                "sellable_quantity": to_int(h.get("ord_psbl_qty")),
                "avg_buy_price": to_float(h.get("pchs_avg_pric")),
                "current_price": to_int(h.get("prpr")),
                "purchase_amount": to_int(h.get("pchs_amt")),
                "eval_amount": to_int(h.get("evlu_amt")),
                "pnl_amount": to_int(h.get("evlu_pfls_amt")),
                "pnl_rate_pct": to_float(h.get("evlu_pfls_rt")),
            }
            for h in raw
            if to_int(h.get("hldg_qty")) > 0
        ]
        return {
            "environment": env,
            "currency": "KRW",
            "holdings": holdings,
            "summary": {
                "cash_deposit": to_int(summary.get("dnca_tot_amt")),
                "cash_d2_settlement": to_int(summary.get("prvs_rcdl_excc_amt")),
                "securities_eval_amount": to_int(summary.get("scts_evlu_amt")),
                "total_eval_amount": to_int(summary.get("tot_evlu_amt")),
                "net_asset_amount": to_int(summary.get("nass_amt")),
                "purchase_total_amount": to_int(summary.get("pchs_amt_smtl_amt")),
                "pnl_total_amount": to_int(summary.get("evlu_pfls_smtl_amt")),
            },
        }

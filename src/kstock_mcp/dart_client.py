"""OpenDART client (https://opendart.fss.or.kr/guide): disclosures, key financials, IPO filings.

Specs used: list.json (공시검색), fnlttSinglAcnt.json (단일회사 주요계정), corpCode.xml (고유번호),
estkRs.json (증권신고서 주요정보 - 지분증권). The API key travels in the query string, so transport
errors are never echoed back (they would include the URL).
"""

from __future__ import annotations

import io
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import httpx2

from .cache import read_json, write_json
from .config import Settings
from .errors import KStockError, network_error
from .kis_client import parse_date, today_kst

BASE = "https://opendart.fss.or.kr/api"
VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
_CORP_TTL_SEC = 7 * 24 * 3600
_MAX_ZIP_BYTES = 50 * 1024 * 1024
_STOCK_RE = re.compile(r"^[0-9A-Z]{6}$")
_REPORT_CODES = {"annual": "11011", "q1": "11013", "half": "11012", "q3": "11014"}
_DISCLOSURE_TYPES = {
    "A": "periodic", "B": "major-matters", "C": "issuance", "D": "ownership", "E": "other",
    "F": "audit", "G": "fund", "H": "abs", "I": "exchange", "J": "ftc",
}


def _amount(value: Any) -> int | None:
    text = str(value or "").replace(",", "").strip()
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


class DartClient:
    def __init__(self, settings: Settings, http: httpx2.Client, clock: Callable[[], float] = time.time):
        self._s = settings
        self._http = http
        self._clock = clock
        self._corp_path = settings.cache_dir / "dart_corp_codes.json"
        self._corps: dict[str, dict[str, str]] | None = None  # stock_code -> {corp_code, name}
        self._memo: dict[tuple, tuple[float, Any]] = {}

    # ------------------------------------------------------------------ transport
    def _request(self, endpoint: str, params: dict[str, str]) -> httpx2.Response:
        self._s.require_dart()
        try:
            resp = self._http.get(f"{BASE}/{endpoint}", params={"crtfc_key": self._s.dart_api_key, **params})
        except httpx2.HTTPError as exc:
            raise network_error("OpenDART", exc) from None
        if resp.status_code != 200:
            raise KStockError(f"OpenDART returned HTTP {resp.status_code}.")
        return resp

    def _json(self, endpoint: str, params: dict[str, str], *, empty_ok: bool = True) -> dict[str, Any]:
        resp = self._request(endpoint, params)
        try:
            body = resp.json()
        except ValueError:
            raise KStockError("OpenDART returned a non-JSON response.") from None
        status = str(body.get("status", ""))
        if status == "013" and empty_ok:  # no data
            return {**body, "list": [], "group": []}
        if status != "000":
            raise KStockError(f"OpenDART error [{status}] {body.get('message', '')}".strip())
        return body

    # ------------------------------------------------------------------ company lookup
    def resolve(self, symbol: str) -> dict[str, str]:
        """6-char stock code, 8-digit corp_code, or exact company name -> {corp_code, name, stock_code}."""
        text = (symbol or "").strip()
        if not text:
            raise KStockError("symbol must not be empty.")
        if re.fullmatch(r"\d{8}", text):
            return {"corp_code": text, "name": "", "stock_code": ""}
        corps = self._load_corps()
        code = text.upper()
        if _STOCK_RE.match(code):
            hit = corps.get(code)
            if not hit:
                raise KStockError(f"No listed company with stock code {code} in OpenDART.")
            return {"stock_code": code, **hit}
        key = "".join(text.split()).casefold()
        matches = [(c, v) for c, v in corps.items() if "".join(v["name"].split()).casefold() == key]
        if len(matches) == 1:
            return {"stock_code": matches[0][0], **matches[0][1]}
        hint = "multiple companies share that name" if matches else "no exact company-name match"
        raise KStockError(f"Could not resolve '{symbol}': {hint}. Use search_stock to get the 6-digit code.")

    def _load_corps(self) -> dict[str, dict[str, str]]:
        if self._corps is not None:
            return self._corps
        cached = read_json(self._corp_path)
        if isinstance(cached, dict) and self._clock() - float(cached.get("fetched_at", 0)) < _CORP_TTL_SEC:
            self._corps = cached["corps"]
            return self._corps
        try:
            self._corps = self._download_corps()
            write_json(self._corp_path, {"fetched_at": self._clock(), "corps": self._corps})
        except KStockError:
            if not isinstance(cached, dict) or not cached.get("corps"):
                raise
            self._corps = cached["corps"]
        return self._corps

    def _download_corps(self) -> dict[str, dict[str, str]]:
        resp = self._request("corpCode.xml", {})
        if len(resp.content) > _MAX_ZIP_BYTES:
            raise KStockError("OpenDART corp-code file is unexpectedly large.")
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                info = zf.infolist()[0]
                if info.file_size > _MAX_ZIP_BYTES:
                    raise KStockError("OpenDART corp-code file is unexpectedly large.")
                xml_bytes = zf.read(info)
        except (zipfile.BadZipFile, IndexError):
            # Errors come back as a small XML <result><status/><message/></result>, not a zip.
            try:
                root = ET.fromstring(resp.content)
                raise KStockError(f"OpenDART error [{root.findtext('status', '')}] {root.findtext('message', '')}".strip())
            except ET.ParseError:
                raise KStockError("OpenDART corp-code response was not a valid zip.") from None
        corps: dict[str, dict[str, str]] = {}
        for item in ET.fromstring(xml_bytes).iter("list"):
            stock = (item.findtext("stock_code") or "").strip()
            if stock:  # listed companies only
                corps[stock] = {
                    "corp_code": (item.findtext("corp_code") or "").strip(),
                    "name": (item.findtext("corp_name") or "").strip(),
                }
        if not corps:
            raise KStockError("OpenDART corp-code file contained no listed companies.")
        return corps

    # ------------------------------------------------------------------ tools
    def get_disclosures(
        self,
        symbol: str,
        days: int = 90,
        kind: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        corp = self.resolve(symbol)
        end = today_kst()
        start = end - timedelta(days=max(1, min(int(days), 3650)))
        params = {
            "corp_code": corp["corp_code"],
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_no": "1",
            "page_count": str(max(1, min(int(limit), 100))),
        }
        if kind:
            k = kind.strip().upper()
            if k not in _DISCLOSURE_TYPES:
                raise KStockError("kind must be one of " + ", ".join(f"{c}={n}" for c, n in _DISCLOSURE_TYPES.items()) + ".")
            params["pblntf_ty"] = k
        body = self._json("list.json", params)
        items = [
            {
                "date": _iso(i.get("rcept_dt", "")),
                "title": i.get("report_nm", "").strip(),
                "filer": i.get("flr_nm", ""),
                "receipt_no": i.get("rcept_no", ""),
                "url": VIEWER + i.get("rcept_no", ""),
                "remark": i.get("rm", ""),
            }
            for i in body.get("list", [])
        ]
        return {
            "company": corp["name"] or (body.get("list") or [{}])[0].get("corp_name", ""),
            "stock_code": corp["stock_code"],
            "corp_code": corp["corp_code"],
            "from": start.isoformat(),
            "to": end.isoformat(),
            "total_count": int(body.get("total_count", len(items)) or 0),
            "disclosures": items,
        }

    def get_financials(
        self,
        symbol: str,
        year: int | None = None,
        report: str = "annual",
        consolidated: bool = True,
    ) -> dict[str, Any]:
        corp = self.resolve(symbol)
        code = _REPORT_CODES.get((report or "").strip().lower())
        if not code:
            raise KStockError("report must be one of: annual, q1, half, q3.")
        year = int(year) if year else today_kst().year - 1
        if year < 2015:
            raise KStockError("OpenDART provides financial data from 2015 onward.")
        body = self._json(
            "fnlttSinglAcnt.json",
            {"corp_code": corp["corp_code"], "bsns_year": str(year), "reprt_code": code},
        )
        rows = body.get("list", [])
        if not rows:
            return {"company": corp["name"], "stock_code": corp["stock_code"], "year": year, "report": report,
                    "statement": None, "accounts": [], "note": "No data returned for this period."}
        want = "CFS" if consolidated else "OFS"
        basis = want if any(r.get("fs_div") == want for r in rows) else rows[0].get("fs_div", "")
        chosen = [r for r in rows if r.get("fs_div") == basis]
        first = chosen[0]
        accounts = [
            {
                "account": r.get("account_nm", ""),
                "statement": r.get("sj_div", ""),  # BS balance sheet, IS income statement
                "current": _amount(r.get("thstrm_amount")),
                "current_cumulative": _amount(r.get("thstrm_add_amount")),
                "previous": _amount(r.get("frmtrm_amount")),
                "before_previous": _amount(r.get("bfefrmtrm_amount")),
            }
            for r in chosen
        ]
        return {
            "company": corp["name"],
            "stock_code": corp["stock_code"] or first.get("stock_code", ""),
            "year": year,
            "report": report.lower(),
            "statement": "consolidated" if basis == "CFS" else "separate",
            "currency": first.get("currency", "KRW"),
            "periods": {
                "current": first.get("thstrm_nm", ""),
                "previous": first.get("frmtrm_nm", ""),
                "before_previous": first.get("bfefrmtrm_nm", ""),
            },
            "receipt_no": first.get("rcept_no", ""),
            "url": VIEWER + first.get("rcept_no", ""),
            "accounts": accounts,
        }

    # ------------------------------------------------------------------ IPO filings (used by ipo.py)
    def list_equity_filings(self, days_back: int, max_pages: int = 3) -> list[dict[str, Any]]:
        """C001 (equity-securities issuance) filings received in the last `days_back` days, newest first.

        Live check (2026-10): list.json ignores pblntf_detail_ty when pblntf_ty is also sent, so only the
        detail code is passed. The result still mixes prospectuses, results reports and withdrawals;
        callers filter on report_nm.
        """
        end = today_kst()
        start = end - timedelta(days=max(1, min(int(days_back), 90)))  # without corp_code DART caps the range at 3 months
        key = ("list", start, end)
        hit = self._memo.get(key)
        if hit and self._clock() - hit[0] < 3600:
            return hit[1]
        result: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            body = self._json(
                "list.json",
                {
                    "bgn_de": start.strftime("%Y%m%d"),
                    "end_de": end.strftime("%Y%m%d"),
                    "pblntf_detail_ty": "C001",
                    "page_no": str(page),
                    "page_count": "100",
                },
            )
            result.extend(body.get("list", []))
            if page >= int(body.get("total_page", 1) or 1):
                break
        self._memo[key] = (self._clock(), result)
        return result

    def equity_filing_detail(self, corp_code: str, since: date) -> dict[str, list[dict[str, Any]]]:
        """estkRs.json groups keyed by title -> records for every filing received since `since`."""
        key = ("estk", corp_code, since)
        hit = self._memo.get(key)
        if hit and self._clock() - hit[0] < 3600:
            return hit[1]
        body = self._json(
            "estkRs.json",
            {
                "corp_code": corp_code,
                "bgn_de": since.strftime("%Y%m%d"),
                "end_de": today_kst().strftime("%Y%m%d"),
            },
        )
        groups = {g.get("title", ""): g.get("list", []) for g in body.get("group", [])}
        self._memo[key] = (self._clock(), groups)
        return groups


def _iso(yyyymmdd: str) -> str:
    try:
        return parse_date(yyyymmdd, today_kst()).isoformat()
    except KStockError:
        return yyyymmdd

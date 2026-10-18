"""IPO schedule from OpenDART equity registration statements (증권신고서, 지분증권).

Source choice: OpenDART is the official, keyed, documented API, so it is preferred over scraping
38.co.kr or KIND. Pre-listing issuers (no stock code yet) that filed an equity registration
statement are treated as IPO candidates; schedule, price and underwriters come from the filing.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .allocation import estimate_equal_allocation
from .dart_client import VIEWER, DartClient
from .errors import KStockError
from .kis_client import today_kst

_DATE_RE = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_NUM_RE = re.compile(r"\d[\d,]*")
_STATUS_RANK = {"subscribing": 0, "upcoming": 1, "unknown": 2, "closed": 3}


def extract_dates(text: str) -> list[date]:
    out = []
    for y, m, d in _DATE_RE.findall(text or ""):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return out


def _numbers(text: str, minimum: int = 1) -> list[int]:
    nums = [int(n.replace(",", "")) for n in _NUM_RE.findall(text or "") if n.replace(",", "")]
    return [n for n in nums if n >= minimum]


def _status(start: date | None, end: date | None, today: date) -> str:
    if not start or not end:
        return "unknown"
    if today < start:
        return "upcoming"
    return "subscribing" if today <= end else "closed"


def build_ipo_schedule(
    dart: DartClient,
    days_back: int = 45,
    limit: int = 10,
    *,
    expected_subscribers: int | None = None,
    general_ratio_pct: float = 25.0,
    equal_ratio_pct: float = 50.0,
    min_subscription_shares: int = 10,
    deposit_rate_pct: float = 50.0,
) -> dict[str, Any]:
    today = today_kst()
    filings = [
        f for f in dart.list_equity_filings(days_back)
        if "증권신고서" in (f.get("report_nm") or "")  # registration statements only (not prospectuses/results)
        and not (f.get("stock_code") or "").strip()  # still unlisted
        and "철" not in (f.get("rm") or "")  # withdrawn
        and "철회" not in (f.get("report_nm") or "")
    ]
    by_corp: dict[str, list[dict]] = {}
    for f in filings:
        by_corp.setdefault(f["corp_code"], []).append(f)
    ordered = sorted(by_corp.values(), key=lambda fs: max(x["rcept_no"] for x in fs), reverse=True)[: max(1, min(limit, 30))]

    items = []
    for fs in ordered:
        latest = max(fs, key=lambda x: x["rcept_no"])
        earliest = min(x["rcept_dt"] for x in fs)
        since = date(int(earliest[:4]), int(earliest[4:6]), int(earliest[6:8]))
        groups = dart.equity_filing_detail(latest["corp_code"], since)
        items.append(_item(latest, groups, today, expected_subscribers, general_ratio_pct, equal_ratio_pct,
                           min_subscription_shares, deposit_rate_pct))
    items.sort(key=lambda i: (_STATUS_RANK[i["status"]], i["subscription_start"] or "9999", i["filed_date"]))
    return {
        "source": "OpenDART equity registration statements (증권신고서-지분증권, C001); pre-listing issuers only",
        "as_of": today.isoformat(),
        "count": len(items),
        "ipos": items,
        "caveats": [
            "Dates, price and underwriters are taken from the latest filing/amendment and can still change "
            "(final offer price is fixed after book-building).",
            "Retail allocation ratios and expected subscriber counts are not in the filing; "
            "equal_allocation_estimate uses the ratios you pass in.",
        ],
    }


def _item(latest: dict, groups: dict, today: date, subscribers: int | None, general_pct: float,
          equal_pct: float, min_shares: int, deposit_pct: float) -> dict[str, Any]:
    rcept = latest["rcept_no"]
    known = {r.get("rcept_no") for rows in groups.values() for r in rows}
    if rcept not in known and known:
        rcept = max(k for k in known if k)  # detail endpoint only has another amendment of this filing

    def recs(title: str) -> list[dict]:
        return [r for r in groups.get(title, []) if r.get("rcept_no") == rcept]

    general = (recs("일반사항") or [{}])[0]
    securities = recs("증권의종류")
    sub_text = general.get("sbd", "")
    dates = extract_dates(sub_text)
    start, end = (min(dates), max(dates)) if dates else (None, None)
    underwriters = [
        {"role": u.get("actsen", ""), "name": u.get("actnmn", ""), "quantity": u.get("udtcnt", "")}
        for u in recs("인수인정보")
    ]
    sec = [
        {
            "type": s.get("stksen", ""), "count": s.get("stkcnt", ""), "par_value": s.get("fv", ""),
            "offer_price": s.get("slprc", ""), "offer_amount": s.get("slta", ""), "method": s.get("slmthn", ""),
        }
        for s in securities
    ]
    item: dict[str, Any] = {
        "company": latest.get("corp_name", ""),
        "corp_code": latest["corp_code"],
        "market_class": latest.get("corp_cls", ""),
        "filing": latest.get("report_nm", "").strip(),
        "filed_date": f"{rcept[:4]}-{rcept[4:6]}-{rcept[6:8]}",
        "url": VIEWER + rcept,
        "status": _status(start, end, today),
        "subscription_period_raw": sub_text,
        "subscription_start": start.isoformat() if start else None,
        "subscription_end": end.isoformat() if end else None,
        "payment_date": general.get("pymd", ""),
        "subscription_notice_date": general.get("sband", ""),
        "allotment_notice_date": general.get("asand", ""),
        "securities": sec,
        "underwriters": underwriters,
    }
    if subscribers:
        item["equal_allocation_estimate"] = _estimate(sec, subscribers, general_pct, equal_pct, min_shares, deposit_pct)
    return item


def _estimate(sec: list[dict], subscribers: int, general_pct: float, equal_pct: float,
              min_shares: int, deposit_pct: float) -> dict[str, Any]:
    first = sec[0] if sec else {}
    counts = _numbers(str(first.get("count", "")))
    prices = _numbers(str(first.get("offer_price", "")), minimum=100)  # ignore stray small numbers
    if len(counts) != 1 or not prices:
        return {"available": False, "reason": "Share count or offer price could not be parsed from the filing "
                "(the price may still be unfixed)."}
    try:
        est = estimate_equal_allocation(
            total_shares=counts[0], offer_price=max(prices), expected_subscribers=subscribers,
            general_ratio_pct=general_pct, equal_ratio_pct=equal_pct,
            min_subscription_shares=min_shares, deposit_rate_pct=deposit_pct,
        )
    except KStockError as exc:
        return {"available": False, "reason": str(exc)}
    est["available"] = True
    est["price_basis"] = "single" if len(set(prices)) == 1 else "band_high (highest figure in the filing's price text)"
    return est

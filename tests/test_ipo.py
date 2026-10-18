from datetime import timedelta

import httpx2

from kstock_mcp.dart_client import DartClient
from kstock_mcp.ipo import build_ipo_schedule, extract_dates
from kstock_mcp.kis_client import today_kst


def fmt_kr(d):
    return f"{d.year}년 {d.month:02d}월 {d.day:02d}일"


def build(settings, make_http, filings, groups_by_corp):
    def handler(req):
        if req.url.path.endswith("/list.json"):
            assert req.url.params["pblntf_detail_ty"] == "C001"
            assert "pblntf_ty" not in req.url.params  # DART ignores the detail code when both are sent
            return httpx2.Response(200, json={"status": "000", "total_page": 1, "list": filings})
        if req.url.path.endswith("/estkRs.json"):
            groups = groups_by_corp.get(req.url.params["corp_code"])
            if groups is None:
                return httpx2.Response(200, json={"status": "013", "message": "no data"})
            return httpx2.Response(200, json={"status": "000", "group": groups})
        return httpx2.Response(404)

    http, seen = make_http(handler)
    return DartClient(settings, http), seen


def filing(corp, name, rcept, stock="", rm="코", report="증권신고서(지분증권)"):
    return {"corp_code": corp, "corp_name": name, "stock_code": stock, "corp_cls": "E", "report_nm": report,
            "rcept_no": rcept, "rcept_dt": rcept[:8], "rm": rm, "flr_nm": name}


def detail(rcept, sbd, count="1,000,000", price="20,000", underwriter="한국투자증권"):
    return [
        {"title": "일반사항", "list": [{"rcept_no": rcept, "sbd": sbd, "pymd": "2099.01.05", "sband": "", "asand": ""}]},
        {"title": "증권의종류", "list": [{"rcept_no": rcept, "stksen": "보통주", "stkcnt": count, "fv": "100",
                                      "slprc": price, "slta": "20,000,000,000", "slmthn": "일반공모"}]},
        {"title": "인수인정보", "list": [{"rcept_no": rcept, "actsen": "대표", "actnmn": underwriter, "udtcnt": "1,000,000"}]},
    ]


def test_extract_dates_formats():
    from datetime import date

    assert extract_dates("2026년 10월 21일 ~ 2026년 10월 22일") == [date(2026, 10, 21), date(2026, 10, 22)]
    assert extract_dates("2026.10.21 ~ 2026.10.22") == [date(2026, 10, 21), date(2026, 10, 22)]
    assert extract_dates("2026-10-21") == [date(2026, 10, 21)]
    assert extract_dates("미정") == []


def test_schedule_filters_classifies_and_sorts(settings, make_http):
    today = today_kst()
    ymd = today.strftime("%Y%m%d")
    up = f"{fmt_kr(today + timedelta(days=5))} ~ {fmt_kr(today + timedelta(days=6))}"
    now = f"{fmt_kr(today - timedelta(days=1))} ~ {fmt_kr(today + timedelta(days=1))}"
    old = f"{fmt_kr(today - timedelta(days=20))} ~ {fmt_kr(today - timedelta(days=19))}"
    filings = [
        filing("00000001", "미래공모", f"{ymd}000001"),
        filing("00000002", "청약중공모", f"{ymd}000002"),
        filing("00000003", "지난공모", f"{ymd}000003"),
        filing("00000004", "이미상장", f"{ymd}000004", stock="123456"),       # listed -> excluded
        filing("00000005", "철회됨", f"{ymd}000005", rm="코철"),               # withdrawn -> excluded
        filing("00000006", "미정공모", f"{ymd}000006"),
        filing("00000001", "미래공모", f"{ymd}000007", report="[기재정정]증권신고서(지분증권)"),  # amendment of #1
        filing("00000007", "채권발행사", f"{ymd}000008", report="투자설명서(일괄신고)"),   # not a registration statement
        filing("00000008", "실적보고사", f"{ymd}000009", report="증권발행실적보고서"),     # results report
    ]
    groups = {
        "00000001": detail(f"{ymd}000007", up, count="2,000,000", price="희망공모가액 10,000원 ~ 12,000원"),
        "00000002": detail(f"{ymd}000002", now),
        "00000003": detail(f"{ymd}000003", old),
        "00000006": detail(f"{ymd}000006", ""),
    }
    d, seen = build(settings, make_http, filings, groups)
    out = build_ipo_schedule(d, expected_subscribers=1_000_000)
    names = [i["company"] for i in out["ipos"]]
    assert names == ["청약중공모", "미래공모", "미정공모", "지난공모"]  # subscribing, upcoming, unknown, closed
    assert [i["status"] for i in out["ipos"]] == ["subscribing", "upcoming", "unknown", "closed"]
    future = out["ipos"][1]
    assert future["filing"].startswith("[기재정정]")  # latest amendment wins
    assert future["subscription_start"] == (today + timedelta(days=5)).isoformat()
    assert future["underwriters"][0]["name"] == "한국투자증권"
    est = future["equal_allocation_estimate"]
    assert est["available"] and "band_high" in est["price_basis"]
    assert est["inputs"]["offer_price"] == 12_000 and est["inputs"]["total_shares"] == 2_000_000
    assert est["formula"] and est["assumptions"]
    assert out["ipos"][0]["equal_allocation_estimate"]["inputs"]["offer_price"] == 20_000
    assert not {"이미상장", "철회됨", "채권발행사", "실적보고사"} & set(names)
    # one list call + one detail call per surviving issuer (4); no per-filing fan-out
    assert sum(r.url.path.endswith("/estkRs.json") for r in seen) == 4


def test_no_estimate_without_subscribers_or_when_unparseable(settings, make_http):
    ymd = today_kst().strftime("%Y%m%d")
    filings = [filing("00000001", "가", f"{ymd}000001")]
    d, _ = build(settings, make_http, filings, {"00000001": detail(f"{ymd}000001", "", price="미정")})
    assert "equal_allocation_estimate" not in build_ipo_schedule(d)["ipos"][0]
    d2, _ = build(settings, make_http, filings, {"00000001": detail(f"{ymd}000001", "", price="미정")})
    est = build_ipo_schedule(d2, expected_subscribers=100)["ipos"][0]["equal_allocation_estimate"]
    assert est["available"] is False and "reason" in est


def test_empty_list(settings, make_http):
    def handler(req):
        return httpx2.Response(200, json={"status": "013", "message": "no data"})

    http, _ = make_http(handler)
    out = build_ipo_schedule(DartClient(settings, http))
    assert out["count"] == 0 and out["ipos"] == []


def test_list_pagination_is_bounded(settings, make_http):
    pages = []

    def handler(req):
        pages.append(req.url.params["page_no"])
        return httpx2.Response(200, json={"status": "000", "total_page": 9, "list": []})

    http, _ = make_http(handler)
    DartClient(settings, http).list_equity_filings(45)
    assert pages == ["1", "2", "3"]

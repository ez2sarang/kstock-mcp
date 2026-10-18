import io
import zipfile

import httpx2
import pytest

from kstock_mcp.dart_client import DartClient
from kstock_mcp.errors import KStockError

CORP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><corp_eng_name>SAMSUNG</corp_eng_name><stock_code>005930</stock_code><modify_date>20260101</modify_date></list>
  <list><corp_code>00164779</corp_code><corp_name>SK하이닉스</corp_name><corp_eng_name>SK hynix</corp_eng_name><stock_code>000660</stock_code><modify_date>20260101</modify_date></list>
  <list><corp_code>99999999</corp_code><corp_name>비상장사</corp_name><corp_eng_name>X</corp_eng_name><stock_code> </stock_code><modify_date>20260101</modify_date></list>
</result>"""


def corp_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", CORP_XML.encode("utf-8"))
    return buf.getvalue()


def dart_http(make_http, routes):
    def handler(req):
        for suffix, fn in routes.items():
            if req.url.path.endswith(suffix):
                return fn(req)
        return httpx2.Response(404)

    return make_http(handler)


CORP_ROUTE = {"/corpCode.xml": lambda r: httpx2.Response(200, content=corp_zip())}


def test_resolve_by_code_name_and_corp_code(settings, make_http):
    http, seen = dart_http(make_http, CORP_ROUTE)
    d = DartClient(settings, http)
    assert d.resolve("005930")["corp_code"] == "00126380"
    assert d.resolve("삼성 전자")["stock_code"] == "005930"
    assert d.resolve("00126380")["corp_code"] == "00126380"
    assert len(seen) == 1  # corp map downloaded once
    with pytest.raises(KStockError, match="search_stock"):
        d.resolve("없는회사")
    with pytest.raises(KStockError):
        d.resolve("123456")


def test_corp_map_excludes_unlisted(settings, make_http):
    http, _ = dart_http(make_http, CORP_ROUTE)
    corps = DartClient(settings, http)._load_corps()
    assert set(corps) == {"005930", "000660"}


def test_corp_download_error_xml(settings, make_http):
    err = "<result><status>010</status><message>등록되지 않은 키입니다.</message></result>"
    http, _ = dart_http(make_http, {"/corpCode.xml": lambda r: httpx2.Response(200, content=err.encode())})
    with pytest.raises(KStockError, match="010"):
        DartClient(settings, http)._load_corps()


def test_disclosures(settings, make_http):
    def lst(req):
        assert req.url.params["corp_code"] == "00126380"
        assert req.url.params["pblntf_ty"] == "A"
        assert req.url.params["page_count"] == "5"
        assert req.url.params["crtfc_key"] == settings.dart_api_key
        return httpx2.Response(200, json={"status": "000", "total_count": 1, "list": [
            {"corp_name": "삼성전자", "report_nm": "사업보고서 (2025.12)", "rcept_no": "20260310000001",
             "flr_nm": "삼성전자", "rcept_dt": "20260310", "rm": "유"}]})

    http, _ = dart_http(make_http, {**CORP_ROUTE, "/list.json": lst})
    out = DartClient(settings, http).get_disclosures("005930", days=30, kind="a", limit=5)
    d = out["disclosures"][0]
    assert d["date"] == "2026-03-10" and d["url"].endswith("rcpNo=20260310000001")
    assert out["company"] == "삼성전자" and out["stock_code"] == "005930"


def test_disclosures_no_data_is_empty_not_error(settings, make_http):
    http, _ = dart_http(make_http, {**CORP_ROUTE, "/list.json": lambda r: httpx2.Response(
        200, json={"status": "013", "message": "조회된 데이타가 없습니다."})})
    assert DartClient(settings, http).get_disclosures("005930")["disclosures"] == []


def test_disclosure_bad_kind(settings, make_http):
    http, _ = dart_http(make_http, CORP_ROUTE)
    with pytest.raises(KStockError, match="kind"):
        DartClient(settings, http).get_disclosures("005930", kind="Z")


def fin_row(fs_div, sj_div, account, cur, prev="900", before="800"):
    return {"rcept_no": "20260310000001", "bsns_year": "2025", "stock_code": "005930", "reprt_code": "11011",
            "account_nm": account, "fs_div": fs_div, "fs_nm": "x", "sj_div": sj_div, "sj_nm": "y",
            "thstrm_nm": "제 57 기", "thstrm_amount": cur, "frmtrm_nm": "제 56 기", "frmtrm_amount": prev,
            "bfefrmtrm_nm": "제 55 기", "bfefrmtrm_amount": before, "currency": "KRW"}


def test_financials_prefers_consolidated(settings, make_http):
    def fin(req):
        assert req.url.params["reprt_code"] == "11011" and req.url.params["bsns_year"] == "2025"
        return httpx2.Response(200, json={"status": "000", "list": [
            fin_row("OFS", "IS", "매출액", "100"),
            fin_row("CFS", "IS", "매출액", "1,000"),
            fin_row("CFS", "BS", "자본총계", "2,000", prev="-"),
        ]})

    http, _ = dart_http(make_http, {**CORP_ROUTE, "/fnlttSinglAcnt.json": fin})
    out = DartClient(settings, http).get_financials("005930", 2025)
    assert out["statement"] == "consolidated"
    assert [(a["account"], a["current"]) for a in out["accounts"]] == [("매출액", 1000), ("자본총계", 2000)]
    assert out["accounts"][1]["previous"] is None  # "-" -> None
    assert out["periods"]["current"] == "제 57 기" and out["url"].endswith("20260310000001")


def test_financials_falls_back_to_separate(settings, make_http):
    http, _ = dart_http(make_http, {**CORP_ROUTE, "/fnlttSinglAcnt.json": lambda r: httpx2.Response(
        200, json={"status": "000", "list": [fin_row("OFS", "IS", "매출액", "100")]})})
    assert DartClient(settings, http).get_financials("005930", 2025)["statement"] == "separate"


def test_financials_validation(settings, make_http):
    http, _ = dart_http(make_http, CORP_ROUTE)
    d = DartClient(settings, http)
    with pytest.raises(KStockError, match="report"):
        d.get_financials("005930", 2025, report="x")
    with pytest.raises(KStockError, match="2015"):
        d.get_financials("005930", 2010)


def test_api_key_never_appears_in_errors(settings, make_http):
    def boom(req):
        raise httpx2.ConnectError("GET " + str(req.url))  # str(url) contains crtfc_key

    http, _ = make_http(boom)
    with pytest.raises(KStockError) as exc:
        DartClient(settings, http)._json("list.json", {})
    assert settings.dart_api_key not in str(exc.value)


def test_dart_error_status_surfaces(settings, make_http):
    http, _ = dart_http(make_http, {"/list.json": lambda r: httpx2.Response(
        200, json={"status": "020", "message": "요청 제한을 초과하였습니다."})})
    with pytest.raises(KStockError, match="020"):
        DartClient(settings, http)._json("list.json", {})


def test_missing_key(tmp_path, make_http):
    from kstock_mcp.config import Settings

    http, _ = make_http(lambda r: httpx2.Response(500))
    with pytest.raises(KStockError, match="DART_API_KEY"):
        DartClient(Settings(cache_dir=tmp_path), http)._json("list.json", {})

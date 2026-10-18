import json
import time

import httpx2
import pytest

from kstock_mcp.errors import KStockError
from kstock_mcp.kis_auth import KISTokenManager
from kstock_mcp.kis_client import KISClient, normalize_code

FUTURE = "2999-01-01 00:00:00"


def token_response(token="tok-1"):
    return httpx2.Response(200, json={"access_token": token, "access_token_token_expired": FUTURE, "expires_in": 86400})


def client_for(settings, make_http, routes):
    """routes: path-suffix -> callable(request)->Response. Token endpoint answered automatically."""

    def handler(req):
        if req.url.path.endswith("/oauth2/tokenP"):
            return token_response()
        for suffix, fn in routes.items():
            if req.url.path.endswith(suffix):
                return fn(req)
        return httpx2.Response(404, json={"msg_cd": "X", "msg1": "unmocked"})

    http, seen = make_http(handler)
    return KISClient(settings, http, sleep=lambda s: None), seen


# ------------------------------------------------------------------ auth
def test_token_issued_once_then_cached_in_memory_and_on_disk(settings, make_http):
    http, seen = make_http(lambda req: token_response())
    mgr = KISTokenManager(settings, http)
    assert mgr.get_token() == "tok-1"
    assert mgr.get_token() == "tok-1"
    assert len(seen) == 1
    # a fresh manager (new process) reuses the file cache: no new request
    mgr2 = KISTokenManager(settings, http)
    assert mgr2.get_token() == "tok-1"
    assert len(seen) == 1


def test_token_request_uses_paper_domain_and_official_body(settings, make_http):
    http, seen = make_http(lambda req: token_response())
    KISTokenManager(settings, http).get_token()
    req = seen[0]
    assert req.url.host == "openapivts.koreainvestment.com" and req.url.port == 29443
    assert json.loads(req.content) == {
        "grant_type": "client_credentials", "appkey": settings.kis_app_key, "appsecret": settings.kis_app_secret,
    }


def test_expired_cache_triggers_reissue(settings, make_http):
    http, seen = make_http(lambda req: httpx2.Response(200, json={"access_token": "t", "expires_in": 86400}))
    clock = [time.time()]
    mgr = KISTokenManager(settings, http, clock=lambda: clock[0])
    mgr.get_token()
    clock[0] += 2 * 86400  # past the one-day validity
    mgr.get_token()
    assert len(seen) == 2


def test_token_file_is_owner_only(settings, make_http):
    http, _ = make_http(lambda req: token_response())
    mgr = KISTokenManager(settings, http)
    mgr.get_token()
    files = list(settings.cache_dir.glob("kis_token_paper_*.json"))
    assert len(files) == 1
    assert files[0].stat().st_mode & 0o077 == 0


def test_token_error_message_has_no_secret(settings, make_http):
    http, _ = make_http(lambda req: httpx2.Response(
        403, json={"error_description": "접근토큰 발급 잔여횟수가 초과되었습니다", "error_code": "EGW00133"}))
    with pytest.raises(KStockError) as exc:
        KISTokenManager(settings, http).get_token()
    assert "EGW00133" in str(exc.value)
    assert settings.kis_app_secret not in str(exc.value)


# ------------------------------------------------------------------ quote
QUOTE_OUT = {"rprs_mrkt_kor_name": "KOSPI200", "stck_prpr": "70,000", "prdy_vrss": "-500", "prdy_vrss_sign": "5",
             "prdy_ctrt": "-0.71", "stck_oprc": "70500", "stck_hgpr": "71000", "stck_lwpr": "69800",
             "stck_sdpr": "70500", "stck_mxpr": "91600", "stck_llam": "49400", "acml_vol": "1234567",
             "acml_tr_pbmn": "86000000000", "w52_hgpr": "80000", "w52_lwpr": "50000", "hts_avls": "4180000",
             "lstn_stcn": "5969782550", "per": "12.5", "pbr": "1.1", "eps": "5600", "bps": "63000"}
BOOK_OUT = {"askp1": "70100", "askp_rsqn1": "100", "bidp1": "70000", "bidp_rsqn1": "200",
            "askp2": "70200", "askp_rsqn2": "50", "total_askp_rsqn": "1000", "total_bidp_rsqn": "2000",
            "aspr_acpt_hour": "153000"}


def quote_routes():
    return {
        "/inquire-price": lambda r: httpx2.Response(200, json={"rt_cd": "0", "output": QUOTE_OUT}),
        "/inquire-asking-price-exp-ccn": lambda r: httpx2.Response(200, json={"rt_cd": "0", "output1": BOOK_OUT, "output2": {}}),
    }


def test_get_quote_with_orderbook(settings, make_http):
    kis, seen = client_for(settings, make_http, quote_routes())
    q = kis.get_quote("005930")
    assert q["price"] == 70000 and q["change"] == -500 and q["change_rate_pct"] == -0.71
    assert q["market_cap_100m_krw"] == 4180000 and q["per"] == 12.5
    assert q["orderbook"]["asks"] == [{"price": 70100, "qty": 100}, {"price": 70200, "qty": 50}]
    assert q["orderbook"]["bids"] == [{"price": 70000, "qty": 200}]
    price_req = next(r for r in seen if r.url.path.endswith("/inquire-price"))
    assert price_req.headers["tr_id"] == "FHKST01010100"
    assert dict(price_req.url.params) == {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}
    assert price_req.headers["authorization"] == "Bearer tok-1"
    assert price_req.headers["custtype"] == "P"


def test_get_quote_without_orderbook_makes_one_data_call(settings, make_http):
    kis, seen = client_for(settings, make_http, quote_routes())
    kis.get_quote("005930", include_orderbook=False)
    assert [r.url.path.rsplit("/", 1)[-1] for r in seen] == ["tokenP", "inquire-price"]


def test_expired_token_is_refreshed_once(settings, make_http):
    calls = {"n": 0}

    def price(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx2.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."})
        return httpx2.Response(200, json={"rt_cd": "0", "output": QUOTE_OUT})

    kis, seen = client_for(settings, make_http, {"/inquire-price": price})
    assert kis.get_quote("005930", include_orderbook=False)["price"] == 70000
    assert sum(r.url.path.endswith("/tokenP") for r in seen) == 2


def test_kis_business_error_is_reported(settings, make_http):
    kis, _ = client_for(settings, make_http, {
        "/inquire-price": lambda r: httpx2.Response(200, json={"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "bad"})})
    with pytest.raises(KStockError, match="OPSQ0002"):
        kis.get_quote("005930", include_orderbook=False)


def test_network_error_does_not_leak_url(settings, make_http):
    def boom(req):
        if req.url.path.endswith("/tokenP"):
            return token_response()
        raise httpx2.ConnectError("failed for appkey=" + settings.kis_app_key)

    http, _ = make_http(boom)
    kis = KISClient(settings, http, sleep=lambda s: None)
    with pytest.raises(KStockError) as exc:
        kis.get_quote("005930", include_orderbook=False)
    assert settings.kis_app_key not in str(exc.value)


@pytest.mark.parametrize("bad", ["", "12345", "ABCDEFG", "삼성전자"])
def test_bad_codes(bad):
    with pytest.raises(KStockError):
        normalize_code(bad)


def test_missing_credentials(tmp_path, make_http):
    from kstock_mcp.config import Settings

    http, _ = make_http(lambda r: httpx2.Response(500))
    kis = KISClient(Settings(cache_dir=tmp_path), http, sleep=lambda s: None)
    with pytest.raises(KStockError, match="KIS_APP_KEY"):
        kis.get_quote("005930")


# ------------------------------------------------------------------ ohlcv
def candle(d, close=100):
    return {"stck_bsop_date": d, "stck_clpr": str(close), "stck_oprc": "99", "stck_hgpr": "101", "stck_lwpr": "98",
            "acml_vol": "10", "acml_tr_pbmn": "1000"}


def test_ohlcv_single_page_sorted_ascending(settings, make_http):
    rows = [candle("20260105"), candle("20260104"), {"stck_bsop_date": ""}]
    kis, seen = client_for(settings, make_http, {
        "/inquire-daily-itemchartprice": lambda r: httpx2.Response(
            200, json={"rt_cd": "0", "output1": {"hts_kor_isnm": "삼성전자"}, "output2": rows})})
    out = kis.get_ohlcv("005930", "daily", "20260101", "20260110")
    assert [c["date"] for c in out["candles"]] == ["2026-01-04", "2026-01-05"]
    assert out["name"] == "삼성전자" and out["truncated"] is False and out["period"] == "D"
    req = seen[-1]
    assert req.headers["tr_id"] == "FHKST03010100"
    assert dict(req.url.params) == {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930", "FID_INPUT_DATE_1": "20260101",
        "FID_INPUT_DATE_2": "20260110", "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0",
    }


def test_ohlcv_pages_backwards_and_caps(settings, make_http):
    from datetime import date, timedelta

    pages = []

    def chart(req):
        end = date(int(req.url.params["FID_INPUT_DATE_2"][:4]), int(req.url.params["FID_INPUT_DATE_2"][4:6]),
                   int(req.url.params["FID_INPUT_DATE_2"][6:]))
        pages.append(end)
        rows = [candle((end - timedelta(days=i)).strftime("%Y%m%d")) for i in range(100)]
        return httpx2.Response(200, json={"rt_cd": "0", "output1": {}, "output2": rows})

    kis, _ = client_for(settings, make_http, {"/inquire-daily-itemchartprice": chart})
    out = kis.get_ohlcv("005930", "D", "20200101", "20260110", max_pages=2)
    assert len(pages) == 2 and out["count"] == 200 and out["truncated"] is True
    assert pages[1] == pages[0] - timedelta(days=100)


def test_ohlcv_validation(settings, make_http):
    kis, _ = client_for(settings, make_http, {})
    with pytest.raises(KStockError, match="period"):
        kis.get_ohlcv("005930", "H")
    with pytest.raises(KStockError, match="start_date"):
        kis.get_ohlcv("005930", "D", "20260110", "20260101")
    with pytest.raises(KStockError, match="Invalid date"):
        kis.get_ohlcv("005930", "D", "yesterday")


# ------------------------------------------------------------------ balance
def test_balance_paginates_and_uses_paper_tr(settings, make_http):
    page = {"n": 0}

    def balance(req):
        page["n"] += 1
        if page["n"] == 1:
            return httpx2.Response(200, headers={"tr_cont": "M"}, json={
                "rt_cd": "0", "ctx_area_fk100": "FK ", "ctx_area_nk100": "NK ",
                "output1": [{"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "10", "pchs_avg_pric": "60000.5",
                             "prpr": "70000", "evlu_amt": "700000", "evlu_pfls_amt": "100000", "evlu_pfls_rt": "16.6"}],
                "output2": [{"dnca_tot_amt": "1000"}]})
        return httpx2.Response(200, headers={"tr_cont": "D"}, json={
            "rt_cd": "0", "output1": [{"pdno": "000660", "prdt_name": "SK하이닉스", "hldg_qty": "0"},
                                      {"pdno": "035420", "prdt_name": "NAVER", "hldg_qty": "2"}],
            "output2": [{"dnca_tot_amt": "5000", "tot_evlu_amt": "900000", "evlu_pfls_smtl_amt": "100000",
                         "nass_amt": "905000"}]})

    kis, seen = client_for(settings, make_http, {"/inquire-balance": balance})
    out = kis.get_balance()
    data_calls = [r for r in seen if r.url.path.endswith("/inquire-balance")]
    assert data_calls[0].headers["tr_id"] == "VTTC8434R"
    assert data_calls[0].headers["tr_cont"] == "" and data_calls[1].headers["tr_cont"] == "N"
    assert data_calls[1].url.params["CTX_AREA_FK100"] == "FK" and data_calls[1].url.params["CTX_AREA_NK100"] == "NK"
    assert [h["code"] for h in out["holdings"]] == ["005930", "035420"]  # zero-quantity row dropped
    assert out["holdings"][0]["avg_buy_price"] == 60000.5
    assert out["summary"]["cash_deposit"] == 5000 and out["summary"]["net_asset_amount"] == 905000
    assert out["environment"] == "paper"
    assert settings.kis_cano not in json.dumps(out)  # account number is never echoed


def test_balance_real_env_uses_real_tr(settings, make_http):
    from dataclasses import replace

    real = replace(settings, kis_env="real")
    kis, seen = client_for(real, make_http, {
        "/inquire-balance": lambda r: httpx2.Response(200, json={"rt_cd": "0", "output1": [], "output2": [{}]})})
    kis.get_balance()
    assert seen[-1].headers["tr_id"] == "TTTC8434R" and seen[-1].url.host == "openapi.koreainvestment.com"


def test_balance_requires_account(settings, make_http):
    from dataclasses import replace

    kis, _ = client_for(replace(settings, kis_cano=""), make_http, {})
    with pytest.raises(KStockError, match="KIS_ACCOUNT_NO"):
        kis.get_balance()

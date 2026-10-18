import io
import time
import zipfile

import httpx2
import pytest

from kstock_mcp.errors import KStockError
from kstock_mcp.stock_master import _FILES, StockMaster, parse_master


def line(code, isin, name, group, tail):
    return f"{code:<9}{isin:<12}{name}" + group + "0" * (tail - 2)


def zip_of(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.mst", text.encode("cp949"))
    return buf.getvalue()


def master_http(make_http):
    kospi = "\r\n".join([
        line("005930", "KR7005930003", "삼성전자", "ST", _FILES["KOSPI"][1]),
        line("005935", "KR7005931001", "삼성전자우", "ST", _FILES["KOSPI"][1]),
        line("069500", "KR7069500007", "KODEX 200", "EF", _FILES["KOSPI"][1]),
        line("006400", "KR7006400006", "삼성SDI", "ST", _FILES["KOSPI"][1]),
    ])
    kosdaq = line("035720", "KR7035720002", "카카오", "ST", _FILES["KOSDAQ"][1])
    konex = line("0070X0", "KR70070X0000", "에스테크엠", "ST", _FILES["KONEX"][1])
    blobs = {"kospi": kospi, "kosdaq": kosdaq, "konex": konex}

    def handler(req):
        for key, text in blobs.items():
            if key in req.url.path:
                return httpx2.Response(200, content=zip_of(text))
        return httpx2.Response(404)

    return make_http(handler)


def test_parse_master_extracts_fields():
    rows = parse_master(line("005930", "KR7005930003", "삼성전자", "ST", 227).encode("cp949"), "KOSPI", 227)
    assert rows == [{"code": "005930", "name": "삼성전자", "market": "KOSPI", "type": "stock",
                     "isin": "KR7005930003", "group_code": "ST"}]


def test_parse_master_skips_short_and_garbled_lines():
    raw = ("short\n" + line("111111", "KR1", "이름", "ST", 227)[:-3] + "\n").encode("cp949")
    assert parse_master(raw, "KOSPI", 227) == []


def test_search_ranking_and_downloads_once(settings, make_http):
    http, seen = master_http(make_http)
    m = StockMaster(settings, http)
    out = m.search("삼성전자")
    assert [r["code"] for r in out["results"]] == ["005930", "005935"]  # exact, then prefix
    assert out["results"][0]["market"] == "KOSPI"
    assert len(seen) == 3
    assert m.search("카카오")["results"][0]["market"] == "KOSDAQ"
    assert len(seen) == 3  # in-memory reuse


def test_search_by_code_and_substring_and_prefers_stock_over_etf(settings, make_http):
    http, _ = master_http(make_http)
    m = StockMaster(settings, http)
    assert m.search("005930")["results"][0]["name"] == "삼성전자"
    assert m.search("삼성")["count"] == 3
    assert [r["code"] for r in m.search("kodex")["results"]] == ["069500"]
    assert m.search(" 삼 성 전 자 ")["results"][0]["code"] == "005930"  # whitespace-insensitive
    assert m.search("없는종목")["results"] == []


def test_file_cache_survives_restart_and_stale_fallback(settings, make_http):
    http, seen = master_http(make_http)
    StockMaster(settings, http).search("삼성")
    n = len(seen)
    StockMaster(settings, http).search("삼성")
    assert len(seen) == n  # fresh file cache

    later = lambda: time.time() + 3 * 86400  # noqa: E731
    failing, _ = make_http(lambda r: httpx2.Response(503))
    stale = StockMaster(settings, failing, clock=later)
    assert stale.search("삼성전자")["count"] == 2  # download failed -> stale cache used


def test_download_failure_without_cache(settings, make_http):
    failing, _ = make_http(lambda r: httpx2.Response(503))
    with pytest.raises(KStockError, match="download failed"):
        StockMaster(settings, failing).search("삼성")


def test_empty_query(settings, make_http):
    http, _ = master_http(make_http)
    with pytest.raises(KStockError):
        StockMaster(settings, http).search("   ")

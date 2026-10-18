import asyncio

import httpx2
import pytest

from kstock_mcp import server
from kstock_mcp.server import NOTICE, App

EXPECTED_TOOLS = {"search_stock", "get_quote", "get_ohlcv", "get_balance", "get_ipo_schedule",
                  "get_disclosure", "get_financials"}


@pytest.fixture
def app(settings, make_http, monkeypatch):
    def handler(req):
        if req.url.path.endswith("/oauth2/tokenP"):
            return httpx2.Response(200, json={"access_token": "t", "expires_in": 86400})
        if req.url.path.endswith("/inquire-price"):
            return httpx2.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "70000"}})
        return httpx2.Response(404)

    http, _ = make_http(handler)
    a = App(settings, http)
    a.kis._sleep = lambda s: None
    monkeypatch.setattr(server, "_app", a)
    return a


def test_exactly_the_seven_read_only_tools_are_registered():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)
    assert not any("order" in t.name or "buy" in t.name or "sell" in t.name for t in tools)


def test_instructions_carry_disclaimer():
    text = server.INSTRUCTIONS.lower()
    assert "not recommend" in text or "does not recommend" in text
    assert "responsibility" in text


def test_tool_adds_notice(app):
    out = server.get_quote("005930", include_orderbook=False)
    assert out["price"] == 70000 and out["notice"] == NOTICE


def test_tool_error_becomes_model_visible_tool_error(app):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="6 characters"):
        server.get_quote("12")


def test_call_through_mcp_layer(app):
    result = asyncio.run(server.mcp.call_tool("get_quote", {"code": "005930", "include_orderbook": False}))
    assert result.is_error is False
    assert result.structured_content["notice"] == NOTICE
    from mcp.server.mcpserver.exceptions import ToolError

    # in-process call_tool raises; over the protocol the SDK turns this into an is_error result
    with pytest.raises(ToolError, match="6 characters"):
        asyncio.run(server.mcp.call_tool("get_quote", {"code": "x"}))


def test_http_logging_cannot_leak_dart_key(settings, make_http, caplog):
    import logging

    from kstock_mcp.dart_client import DartClient

    http, _ = make_http(lambda r: httpx2.Response(200, json={"status": "000", "list": []}))
    with caplog.at_level(logging.INFO):
        DartClient(settings, http)._json("list.json", {})
    assert settings.dart_api_key not in caplog.text

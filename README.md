# K-Stock MCP

[한국어 README](README.ko.md)

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server that plugs the Korean stock market into Claude and other MCP agents: **quotes and charts from the KIS Open API, disclosures and financials from OpenDART, and an IPO (공모주) schedule with an equal-allocation (균등배정) calculator.**

- Token handling for KIS is automatic: issued once, cached on disk (24 h validity), paper (mock) and real environments kept apart, **paper is the default**.
- Data and calculations only. **No order tools. No buy/sell recommendations.**
- stdio transport, runs with `uvx`, no hosted service: your keys never leave your machine.

> **Disclaimer.** Everything this server returns is informational data, not investment advice. It is not an investment advisory or discretionary service, and it does not recommend securities. Investment decisions and their consequences are entirely your own responsibility. Data may be delayed, incomplete or wrong. Not affiliated with Korea Investment & Securities or the Financial Supervisory Service.

## Tools (v0.1, all read-only)

| Tool | What it does | Source |
|---|---|---|
| `search_stock` | Company name or code to code, market and type | KIS public symbol master files |
| `get_quote` | Price, day range, volume, PER/PBR/EPS/BPS, 10-level order book | KIS `inquire-price`, `inquire-asking-price-exp-ccn` |
| `get_ohlcv` | Daily/weekly/monthly/yearly candles, optionally adjusted | KIS `inquire-daily-itemchartprice` |
| `get_balance` | Holdings and account summary (paper by default) | KIS `inquire-balance` |
| `get_ipo_schedule` | Upcoming/ongoing IPOs: subscription dates, price, underwriters, optional equal-allocation estimate | OpenDART equity registration statements |
| `get_disclosure` | Recent filings for a company, with DART viewer links | OpenDART `list.json` |
| `get_financials` | Key accounts (assets, liabilities, equity, revenue, operating income, net income) | OpenDART `fnlttSinglAcnt.json` |

Every response carries a short `notice` field with the disclaimer, and the server `instructions` tell the model not to present output as advice.

## Install in 3 steps

**1. Get keys** (both free)

- KIS Open API app key and secret from [apiportal.koreainvestment.com](https://apiportal.koreainvestment.com). Start with **paper (mock-trading) keys**; they are separate from real keys.
- OpenDART key from [opendart.fss.or.kr](https://opendart.fss.or.kr).

**2. Install [uv](https://docs.astral.sh/uv/)** and check the server starts:

```bash
uvx --from git+https://github.com/ez2sarang/kstock-mcp kstock-mcp --version
```

(After a PyPI release this becomes `uvx kstock-mcp`.)

**3. Add it to your client**

Claude Code:

```bash
claude mcp add kstock \
  --env KIS_ENV=paper \
  --env KIS_APP_KEY=your-kis-app-key \
  --env KIS_APP_SECRET=your-kis-app-secret \
  --env KIS_ACCOUNT_NO=12345678-01 \
  --env DART_API_KEY=your-dart-api-key \
  -- uvx --from git+https://github.com/ez2sarang/kstock-mcp kstock-mcp
```

Claude Desktop (`claude_desktop_config.json`; use the full path to `uvx` if the app cannot find it):

```json
{
  "mcpServers": {
    "kstock": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/ez2sarang/kstock-mcp", "kstock-mcp"],
      "env": {
        "KIS_ENV": "paper",
        "KIS_APP_KEY": "your-kis-app-key",
        "KIS_APP_SECRET": "your-kis-app-secret",
        "KIS_ACCOUNT_NO": "12345678-01",
        "DART_API_KEY": "your-dart-api-key"
      }
    }
  }
}
```

You only need the keys for the tools you use: DART tools need `DART_API_KEY`; KIS tools need the KIS variables; `get_balance` also needs the account number; `search_stock` needs no key.

## Configuration

| Variable | Meaning |
|---|---|
| `KIS_ENV` | `paper` (default) or `real`. Anything else is rejected rather than silently treated as real |
| `KIS_APP_KEY`, `KIS_APP_SECRET` | KIS credentials for the chosen environment |
| `KIS_ACCOUNT_NO` | `12345678-01`, `1234567801` or `12345678` (product code defaults to `01`). Or use `KIS_CANO` + `KIS_ACNT_PRDT_CD` |
| `DART_API_KEY` | OpenDART key |
| `KSTOCK_MCP_CACHE_DIR` | Cache directory (default `~/.cache/kstock-mcp`): KIS token (mode 0600), symbol master (1 day), DART company codes (7 days) |

## IPO schedule and equal allocation

Source choice: `get_ipo_schedule` uses OpenDART equity registration statements (증권신고서, 지분증권) because it is the official, documented, keyed API, so there is no scraping and no robots.txt/terms question. Pre-listing issuers that filed one are treated as IPO candidates. Dates, price text and underwriters come from the latest filing or amendment and can still change before book-building ends. The filing does not contain retail allocation ratios or subscriber counts.

Pass `expected_subscribers` (and optionally `general_ratio_pct`, `equal_ratio_pct`, `min_subscription_shares`, `deposit_rate_pct`) to get an `equal_allocation_estimate` per IPO. The calculation is a pure function (`kstock_mcp.allocation.estimate_equal_allocation`) and the response lists its formula and assumptions:

```
general_shares     = floor(total_shares * general_ratio_pct / 100)
equal_shares       = floor(general_shares * equal_ratio_pct / 100)
guaranteed_each    = floor(equal_shares / expected_subscribers)
leftover (lottery) = equal_shares mod expected_subscribers
```

Real allocations are decided per underwriter, so treat the output as a rough estimate.

## Privacy

The server runs locally over stdio. It sends your KIS key/secret to the KIS Open API, your DART key to OpenDART, and downloads KIS's public symbol master files. It has no telemetry and no server of its own. Keys are read from environment variables only, are never written to logs, and error messages never include request URLs.

## Development

```bash
git clone https://github.com/ez2sarang/kstock-mcp && cd kstock-mcp
uv sync
uv run pytest        # all HTTP is mocked; no network, no keys needed
```

Dependencies are pinned to exact versions in `pyproject.toml` and `uv.lock`. The server uses `mcp` 2.x, where the former `FastMCP` class is `MCPServer`.

## Roadmap

v0.2 and later: condition search, portfolio report, IPO alerts. This server stays read-only: order placement is not planned.

## License

[MIT](LICENSE)

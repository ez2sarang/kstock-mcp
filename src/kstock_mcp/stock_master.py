"""Stock name -> code search backed by KIS's public master files.

KIS publishes daily symbol master files (kospi/kosdaq/konex_code.mst.zip, see
https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info). They are fixed-width
cp949 text: 9 chars short code, 12 chars standard code (ISIN), then the Korean name, followed by a
fixed-length tail whose first two characters are the security group code (ST = common stock ...).
KIS offers no name-search REST endpoint, so this is the official way to resolve names to codes.
"""

from __future__ import annotations

import io
import time
import zipfile
from collections.abc import Callable
from typing import Any

import httpx2

from .cache import read_json, write_json
from .config import Settings
from .errors import KStockError, network_error

_BASE = "https://new.real.download.dws.co.kr/common/master"
# market -> (file, tail length after the name, excluding the line break; verified against the live files)
_FILES = {
    "KOSPI": ("kospi_code.mst.zip", 227),
    "KOSDAQ": ("kosdaq_code.mst.zip", 221),
    "KONEX": ("konex_code.mst.zip", 184),
}
_GROUPS = {
    "ST": "stock", "EF": "ETF", "EN": "ETN", "BC": "beneficiary-certificate", "RT": "REIT",
    "FS": "foreign-stock", "DR": "DR", "MF": "mutual-fund-company", "SC": "ship-fund",
    "IF": "infra-fund", "SW": "warrant", "SR": "subscription-right", "EW": "ELW",
}
_TTL_SEC = 24 * 3600
_MAX_ZIP_BYTES = 20 * 1024 * 1024


def normalize(text: str) -> str:
    return "".join(text.split()).casefold()


class StockMaster:
    def __init__(self, settings: Settings, http: httpx2.Client, clock: Callable[[], float] = time.time):
        self._http = http
        self._clock = clock
        self._path = settings.cache_dir / "stock_master.json"
        self._entries: list[dict[str, Any]] | None = None

    def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        q = normalize(query or "")
        if not q:
            raise KStockError("query must not be empty (company name or code).")
        limit = max(1, min(int(limit), 50))
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for e in self._load():
            name = e["_n"]
            if q == e["code"].casefold():
                rank = 0
            elif name == q:
                rank = 1
            elif name.startswith(q):
                rank = 2
            elif q in name:
                rank = 3
            else:
                continue
            # Prefer common stocks over funds/ETPs, then shorter names (closer matches).
            scored.append((rank, 0 if e["group_code"] == "ST" else 1, len(name), e))
        scored.sort(key=lambda t: t[:3])
        results = [
            {k: v for k, v in e.items() if not k.startswith("_") and k != "group_code"}
            for _, _, _, e in scored[:limit]
        ]
        return {"query": query, "count": len(results), "total_matches": len(scored), "results": results}

    # ------------------------------------------------------------------ loading
    def _load(self) -> list[dict[str, Any]]:
        if self._entries is not None:
            return self._entries
        cached = read_json(self._path)
        fresh = isinstance(cached, dict) and self._clock() - float(cached.get("fetched_at", 0)) < _TTL_SEC
        if fresh:
            entries = cached["entries"]
        else:
            try:
                entries = self._download()
                write_json(self._path, {"fetched_at": self._clock(), "entries": entries})
            except KStockError:
                if not isinstance(cached, dict) or not cached.get("entries"):
                    raise
                entries = cached["entries"]  # stale beats nothing
        for e in entries:
            e["_n"] = normalize(e["name"])
        self._entries = entries
        return entries

    def _download(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for market, (fname, tail) in _FILES.items():
            try:
                resp = self._http.get(f"{_BASE}/{fname}")
            except httpx2.HTTPError as exc:
                raise network_error("KIS symbol master", exc) from None
            if resp.status_code != 200 or len(resp.content) > _MAX_ZIP_BYTES:
                raise KStockError(f"KIS symbol master download failed for {market} (HTTP {resp.status_code}).")
            entries.extend(parse_master(_unzip(resp.content), market, tail))
        if not entries:
            raise KStockError("KIS symbol master contained no entries.")
        return entries


def _unzip(blob: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            info = zf.infolist()[0]
            if info.file_size > _MAX_ZIP_BYTES:
                raise KStockError("KIS symbol master file is unexpectedly large.")
            return zf.read(info)
    except (zipfile.BadZipFile, IndexError):
        raise KStockError("KIS symbol master file is not a valid zip.") from None


def parse_master(raw: bytes, market: str, tail: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in raw.decode("cp949", errors="replace").splitlines():
        if len(line) <= 21 + tail:
            continue
        group = line[len(line) - tail : len(line) - tail + 2]
        if not group.isalnum():
            continue  # layout drift guard: skip rather than return garbage
        code = line[0:9].strip()
        name = line[21 : len(line) - tail].strip()
        if not code or not name:
            continue
        out.append({
            "code": code,
            "name": name,
            "market": market,
            "type": _GROUPS.get(group, group),
            "isin": line[9:21].strip(),
            "group_code": group,
        })
    return out

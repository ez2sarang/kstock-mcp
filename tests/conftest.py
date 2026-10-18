"""Shared fixtures. All HTTP is mocked with httpx2.MockTransport: tests never touch the network."""

from __future__ import annotations

import httpx2
import pytest

from kstock_mcp.config import Settings

FAKE_APP_KEY = "test-app-key-not-real"
FAKE_APP_SECRET = "test-app-secret-not-real"
FAKE_DART_KEY = "test-dart-key-not-real"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        kis_env="paper",
        kis_app_key=FAKE_APP_KEY,
        kis_app_secret=FAKE_APP_SECRET,
        kis_cano="00000000",
        kis_acnt_prdt_cd="01",
        dart_api_key=FAKE_DART_KEY,
        cache_dir=tmp_path / "cache",
    )


@pytest.fixture
def make_http():
    """make_http(handler) -> (client, requests_seen)."""

    def build(handler):
        seen: list[httpx2.Request] = []

        def wrapped(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return handler(request)

        return httpx2.Client(transport=httpx2.MockTransport(wrapped)), seen

    return build

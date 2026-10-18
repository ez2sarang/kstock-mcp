"""Shared error type. Messages are shown to the model/user and must never contain secrets."""

from __future__ import annotations


class KStockError(Exception):
    """A user-facing failure (bad input, missing configuration, upstream API error)."""


def network_error(service: str, exc: Exception) -> KStockError:
    """Wrap a transport exception without echoing its text (it can contain request URLs/keys)."""
    return KStockError(f"{service} request failed ({type(exc).__name__}). Check your network and try again.")

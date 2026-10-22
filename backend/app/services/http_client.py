"""Shared HTTPX defaults for outbound provider requests."""

import ssl
from typing import Any

import httpx


def create_outbound_client(**kwargs: Any) -> httpx.AsyncClient:
    """Use the runtime's system CA store and identify requests to public APIs."""
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("User-Agent", "imaginAI/1.0 (media-source retrieval)")
    kwargs.setdefault("headers", headers)
    kwargs.setdefault("verify", ssl.create_default_context())
    return httpx.AsyncClient(**kwargs)

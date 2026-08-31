"""Single-request provider health checks used by the BYOK settings screen."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from app.config import GEMINI_TEXT_MODEL


def _classify(status_code: int) -> str:
    if status_code in (401, 403):
        return "unauthorized"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "unavailable"
    return "invalid"


def _result(status: str, message: str = "") -> dict[str, Any]:
    return {"status": status, "message": message[:180]}


async def _test_http(url: str, headers: dict[str, str], params: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(url, headers=headers, params=params)
        if 200 <= response.status_code < 300:
            return _result("valid", "Provider accepted the key.")
        return _result(_classify(response.status_code), f"Provider returned HTTP {response.status_code}.")
    except (httpx.TimeoutException, httpx.NetworkError):
        return _result("unavailable", "Provider did not respond within the test timeout.")
    except Exception:
        return _result("unavailable", "Provider test could not be completed.")


async def _test_gemini(api_key: str) -> dict[str, Any]:
    def call() -> None:
        from google import genai
        client = genai.Client(api_key=api_key)
        client.models.generate_content(model=GEMINI_TEXT_MODEL, contents="Reply with OK.")

    try:
        await asyncio.wait_for(asyncio.to_thread(call), timeout=15)
        return _result("valid", "Gemini accepted the key.")
    except asyncio.TimeoutError:
        return _result("unavailable", "Gemini did not respond within the test timeout.")
    except Exception as exc:
        text = str(exc).lower()
        if "429" in text or "rate" in text or "quota" in text:
            return _result("rate_limited", "Gemini rate limited the test request.")
        if "401" in text or "403" in text or "permission" in text or "api key" in text:
            return _result("unauthorized", "Gemini rejected the key.")
        return _result("invalid", "Gemini rejected the test request.")


async def _test_groq(api_key: str) -> dict[str, Any]:
    return await _test_http(
        "https://api.groq.com/openai/v1/models",
        {"Authorization": f"Bearer {api_key}"},
    )


async def _test_fish(api_key: str) -> dict[str, Any]:
    return await _test_http(
        "https://api.fish.audio/model?page_size=1",
        {"Authorization": f"Bearer {api_key}"},
    )


async def test_provider(provider: str, api_key: str) -> dict[str, Any]:
    """Run exactly one cheap request. No retries and no generated media is kept."""
    provider = provider.lower()
    if provider == "gemini":
        return await _test_gemini(api_key)
    if provider == "groq":
        return await _test_groq(api_key)
    if provider == "fish-audio":
        return await _test_fish(api_key)
    if provider == "pexels":
        return await _test_http(
            "https://api.pexels.com/v1/search",
            {"Authorization": api_key},
            {"query": "nature", "per_page": "1"},
        )
    if provider == "pixabay":
        return await _test_http(
            "https://pixabay.com/api/",
            {},
            {"key": api_key, "q": "nature", "per_page": "3"},
        )
    if provider == "nasa":
        return await _test_http(
            "https://api.nasa.gov/planetary/apod",
            {},
            {"api_key": api_key, "date": "2020-01-01"},
        )
    return _result("invalid", "Unsupported provider.")
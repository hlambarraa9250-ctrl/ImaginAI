"""Device-scoped BYOK storage.

User provider keys are encrypted before they are written to SQLite. They are
never included in API responses; callers receive only status metadata.
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet

from app.db import get_db_connection

PROVIDERS = ("gemini", "fish-audio", "pexels", "pixabay", "nasa", "groq", "coverr", "nara")
MAX_GEMINI_KEYS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cipher() -> Fernet:
    secret = os.getenv("SESSION_SECRET", "").strip()
    if not secret:
        raise RuntimeError("SESSION_SECRET is required to store user API keys securely.")
    derived = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(derived)


def _validate_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    return normalized


def _decrypt(row: Any) -> str:
    return _cipher().decrypt(row["encrypted_key"].encode("utf-8")).decode("utf-8")


def get_user_keys(device_id: str, provider: str) -> list[str]:
    """Return decrypted keys for backend use, ordered by creation time."""
    provider = _validate_provider(provider)
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT encrypted_key FROM api_keys
            WHERE device_id = ? AND provider = ?
            ORDER BY created_at ASC
            """,
            (device_id, provider),
        ).fetchall()
    return [_decrypt(row) for row in rows]


def get_user_key_by_id(device_id: str, provider: str, key_id: str) -> str:
    provider = _validate_provider(provider)
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT encrypted_key FROM api_keys
            WHERE device_id = ? AND provider = ? AND key_id = ?
            """,
            (device_id, provider, key_id),
        ).fetchone()
    if not row:
        raise KeyError("API key not found.")
    return _decrypt(row)


def save_user_key(device_id: str, provider: str, api_key: str) -> dict[str, Any]:
    provider = _validate_provider(provider)
    value = api_key.strip()
    if not value:
        raise ValueError("API key cannot be empty.")

    key_id = f"key_{uuid.uuid4().hex[:12]}"
    now = _now()
    encrypted = _cipher().encrypt(value.encode("utf-8")).decode("utf-8")
    with get_db_connection() as conn:
        if provider != "gemini":
            conn.execute(
                "DELETE FROM api_keys WHERE device_id = ? AND provider = ?",
                (device_id, provider),
            )
        else:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM api_keys WHERE device_id = ? AND provider = ?",
                (device_id, provider),
            ).fetchone()["count"]
            if count >= MAX_GEMINI_KEYS:
                raise ValueError(f"You can store up to {MAX_GEMINI_KEYS} Gemini keys.")

        conn.execute(
            """
            INSERT INTO api_keys
              (device_id, provider, key_id, encrypted_key, status, last_error, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'untested', NULL, ?, ?)
            """,
            (device_id, provider, key_id, encrypted, now, now),
        )
        conn.commit()

    return {"key_id": key_id, "provider": provider, "status": "untested"}


def delete_user_key(device_id: str, provider: str, key_id: str) -> bool:
    provider = _validate_provider(provider)
    with get_db_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM api_keys WHERE device_id = ? AND provider = ? AND key_id = ?",
            (device_id, provider, key_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def record_test_result(
    device_id: str,
    provider: str,
    key_id: str,
    status: str,
    error: str | None = None,
) -> None:
    provider = _validate_provider(provider)
    allowed_statuses = {"untested", "valid", "invalid", "rate_limited", "unauthorized", "unavailable"}
    safe_status = status if status in allowed_statuses else "invalid"
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE api_keys
            SET status = ?, last_error = ?, updated_at = ?, last_tested_at = ?
            WHERE device_id = ? AND provider = ? AND key_id = ?
            """,
            (safe_status, (error or "")[:180], _now(), _now(), device_id, provider, key_id),
        )
        conn.commit()


def list_key_metadata(device_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT provider, key_id, status, last_tested_at, created_at
            FROM api_keys WHERE device_id = ? ORDER BY created_at ASC
            """,
            (device_id,),
        ).fetchall()

    for provider in PROVIDERS:
        provider_rows = [dict(row) for row in rows if row["provider"] == provider]
        result[provider] = {
            "configured": bool(provider_rows),
            "keys": [
                {
                    "key_id": row["key_id"],
                    "status": row["status"],
                    "last_tested_at": row["last_tested_at"],
                    "created_at": row["created_at"],
                }
                for row in provider_rows
            ],
        }
    return result
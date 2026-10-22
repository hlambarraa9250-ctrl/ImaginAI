"""
imaginAI — Stage Protection: Rate Limiting & Safety Net Service
Manages the site-wide global daily limit (20 videos / day).
Uses JSON storage in backend/data/ for simple, file-based persistence without database dependencies.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

GLOBAL_LIMIT_FILE = DATA_DIR / "global_limits.json"

# Keep a site-wide limit to protect shared provider usage and costs.
MAX_GLOBAL_VIDEOS_PER_DAY = 20


# ── File I/O Helpers ─────────────────────────────────────────────────────────

def _load_json(file_path: Path) -> dict:
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(file_path: Path, data: dict) -> None:
    try:
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[RateLimit Warning] Failed to save {file_path.name}: {e}")


# ── Global Check Function ────────────────────────────────────────────────────

def check_and_increment_rate_limits(visitor_id: str) -> tuple[bool, str]:
    """
    Check the site-wide daily limit. The visitor argument remains for compatibility.
    If allowed, increment the global counter and return (True, "").
    If restricted, return (False, "Arabic error message").
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    global_data = _load_json(GLOBAL_LIMIT_FILE)
    if global_data.get("date") != today_str:
        global_data = {"date": today_str, "count": 0}

    current_global_count = int(global_data.get("count", 0))

    if current_global_count >= MAX_GLOBAL_VIDEOS_PER_DAY:
        return (
            False,
            f"وصل النظام التجريبي للحد الأقصى اليومي ({MAX_GLOBAL_VIDEOS_PER_DAY} فيديو لكامل الموقع). يرجى العودة غداً!",
        )

    global_data["count"] = current_global_count + 1

    _save_json(GLOBAL_LIMIT_FILE, global_data)

    print(f"[RateLimit] Approved job. Global today: {global_data['count']}/{MAX_GLOBAL_VIDEOS_PER_DAY}")
    return (True, "")

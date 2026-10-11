"""
imaginAI Backend — Configuration
Loads environment variables and provides accessor functions.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env from backend root ──────────────────────────────────────────────
_backend_dir = Path(__file__).resolve().parent.parent
_env_path = _backend_dir / ".env"
if not _env_path.exists():
    # Fallback: check project root
    _env_path = _backend_dir.parent / ".env"
    if not _env_path.exists():
        _env_path = _backend_dir.parent / "env"  # user's current file name
load_dotenv(_env_path)

# ── Directories ──────────────────────────────────────────────────────────────
JOBS_DIR = _backend_dir / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

STORAGE_DIR = _backend_dir / "storage"
STORAGE_DIR.mkdir(exist_ok=True)

DATA_DIR = _backend_dir / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── API Keys (never hardcoded) ───────────────────────────────────────────────
def get_gemini_keys(device_id: str | None = None) -> list[str]:
    """
    Get list of Gemini API keys for failover rotation.
    Supports:
    1. GEMINI_API_KEYS (comma-separated: key1,key2,key3)
    2. Individual vars GEMINI_API_KEY, GEMINI_API_KEY_1, GEMINI_API_KEY_2 ... GEMINI_API_KEY_10
    """
    if device_id:
        try:
            from app.api_keys import get_user_keys
            user_keys = get_user_keys(device_id, "gemini")
            if user_keys:
                return user_keys
        except Exception:
            # A missing encryption secret must not prevent the app from using
            # intentionally configured server-side fallback keys.
            pass

    keys = []
    
    # 1. Check comma-separated GEMINI_API_KEYS
    raw_keys = os.getenv("GEMINI_API_KEYS", "")
    if raw_keys:
        keys.extend([k.strip() for k in raw_keys.split(",") if k.strip()])

    # 2. Check individual GEMINI_API_KEY and GEMINI_API_KEY_1..10
    main_key = os.getenv("GEMINI_API_KEY", "").strip()
    if main_key and main_key not in keys:
        keys.append(main_key)

    for i in range(1, 11):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if k and k not in keys:
            keys.append(k)

    if not keys:
        raise RuntimeError("No Gemini API keys found! Set GEMINI_API_KEY or GEMINI_API_KEYS in .env")

    # BYOK and server-side failover are intentionally bounded.
    return keys[:5]


def get_gemini_key(device_id: str | None = None) -> str:
    """Get the primary Gemini API key."""
    return get_gemini_keys(device_id=device_id)[0]

def _get_provider_key(provider: str, env_name: str, device_id: str | None = None) -> str:
    if device_id:
        try:
            from app.api_keys import get_user_keys
            user_keys = get_user_keys(device_id, provider)
            if user_keys:
                return user_keys[0]
        except Exception:
            pass
    key = os.getenv(env_name)
    if not key:
        raise RuntimeError(f"{env_name} is not set in .env")
    return key

def get_pexels_key(device_id: str | None = None) -> str:
    return _get_provider_key("pexels", "PEXELS_API_KEY", device_id)

def get_pixabay_key(device_id: str | None = None) -> str:
    return _get_provider_key("pixabay", "PIXABAY_API_KEY", device_id)

def get_groq_key(device_id: str | None = None) -> str:
    return _get_provider_key("groq", "GROQ_API_KEY", device_id)

def get_fish_audio_key(device_id: str | None = None) -> str:
    return _get_provider_key("fish-audio", "FISH_AUDIO_API_KEY", device_id)

def get_nasa_key(device_id: str | None = None) -> str:
    return _get_provider_key("nasa", "NASA_API_KEY", device_id)

def get_coverr_key(device_id: str | None = None) -> str:
    return _get_provider_key("coverr", "COVERR_API_KEY", device_id)

def get_nara_key(device_id: str | None = None) -> str:
    return _get_provider_key("nara", "NARA_API_KEY", device_id)

COVERR_ENABLED = os.getenv("COVERR_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

# ── Model Constants ──────────────────────────────────────────────────────────

GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
GEMINI_SCRIPT_MODEL = GEMINI_TEXT_MODEL
GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_TTS_VOICE = "Kore"
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"

# ── Voice Catalog Definition ───────────────────────────────────────────────
VOICE_PROVIDERS = [
    {
        "provider": "edge-tts",
        "provider_name": "Edge-TTS (مجاني وعالي الجودة)",
        "desc": "أصوات سريعة ومجانية 100% بدون قيود أو حدود API",
        "voices": [
            {
                "id": "ar-SA-HamedNeural",
                "name": "حامد (Hamed)",
                "gender": "male",
                "lang": "العربية",
                "desc": "صوت ذكوري فصيح هادئ ومناسب للسرد والوثائقيات",
            },
            {
                "id": "ar-EG-SalmaNeural",
                "name": "سلمى (Salma)",
                "gender": "female",
                "lang": "العربية",
                "desc": "صوت أنثوي فصيح وطبيعي للقصص",
            },
            {
                "id": "ar-SA-ZariyahNeural",
                "name": "زارية (Zariyah)",
                "gender": "female",
                "lang": "العربية",
                "desc": "صوت أنثوي رزين واحترافي",
            },
            {
                "id": "ar-AE-HamdanNeural",
                "name": "حمدان (Hamdan)",
                "gender": "male",
                "lang": "العربية",
                "desc": "صوت ذكوري حماسي للقصص التحفيزية",
            },
            {
                "id": "en-US-ChristopherNeural",
                "name": "كريستوفر (Christopher)",
                "gender": "male",
                "lang": "English",
                "desc": "Deep documentary voice",
            },
            {
                "id": "en-US-JennyNeural",
                "name": "جيني (Jenny)",
                "gender": "female",
                "lang": "English",
                "desc": "Clear professional female voice",
            },
        ],
    },
    {
        "provider": "gemini",
        "provider_name": "Google Gemini (imaginAI Mini)",
        "desc": "أصوات الذكاء الاصطناعي الرسمية المتاحة بموديل Gemini TTS",
        "voices": [
            {
                "id": "Kore",
                "name": "كوري (Kore)",
                "gender": "female",
                "lang": "متعدد اللغات",
                "desc": "صوت أنثوي طبيعي وحيوي مناسب للقصص والسرد",
            },
            {
                "id": "Puck",
                "name": "بوك (Puck)",
                "gender": "male",
                "lang": "متعدد اللغات",
                "desc": "صوت ذكوري رزين وهادئ",
            },
            {
                "id": "Charon",
                "name": "كارون (Charon)",
                "gender": "male",
                "lang": "متعدد اللغات",
                "desc": "صوت ذكوري عميق ودافئ للوثائقيات",
            },
            {
                "id": "Fenrir",
                "name": "فينرير (Fenrir)",
                "gender": "male",
                "lang": "متعدد اللغات",
                "desc": "صوت ذكوري قوي ومباشر",
            },
            {
                "id": "Aoede",
                "name": "أويدي (Aoede)",
                "gender": "female",
                "lang": "متعدد اللغات",
                "desc": "صوت أنثوي سينمائي ومميز",
            },
            {
                "id": "Leda",
                "name": "ليدا (Leda)",
                "gender": "female",
                "lang": "متعدد اللغات",
                "desc": "صوت أنثوي واضح ورقيق",
            },
            {
                "id": "Orus",
                "name": "اوروس (Orus)",
                "gender": "male",
                "lang": "متعدد اللغات",
                "desc": "صوت ذكوري رسمي ورصين",
            },
            {
                "id": "Zephyr",
                "name": "زفير (Zephyr)",
                "gender": "female",
                "lang": "متعدد اللغات",
                "desc": "صوت أنثوي هادئ وناعم",
            },
        ],
    },
    {
        "provider": "fish-audio",
        "provider_name": "Fish Audio",
        "desc": "أصوات فائقة الجودة من منصة Fish Audio المتاحة لحسابك",
        "voices": [
            {
                "id": "default",
                "name": "Fish Audio Default Voice",
                "gender": "neutral",
                "lang": "العربية / English",
                "desc": "الصوت الافتراضي الأساسي لمنصة Fish Audio",
            }
        ],
    },
]



# ── Style Template Configuration ─────────────────────────────────────────────
# Max seconds per shot before splitting into multiple shots
STYLE_MAX_SHOT_DURATION: dict[str, float] = {
    "mystery":       5.0,
    "listicle":      5.0,
    "documentary":  10.0,
    "motivational":  7.0,
}

# xfade transition type + duration (seconds). None = hard cut (0s).
STYLE_TRANSITION: dict[str, dict] = {
    "mystery":      {"type": "fade",         "duration": 0.3},
    "listicle":     {"type": None,           "duration": 0.0},   # hard cut
    "documentary":  {"type": "dissolve",     "duration": 0.5},
    "motivational": {"type": "fade",         "duration": 0.3},
}

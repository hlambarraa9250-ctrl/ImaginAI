"""
imaginAI — Stage 3: Footage Retrieval
Prioritizes archival/public media before stock providers.
Downloads locally — no hotlinking.
Filters for horizontal videos (orientation=landscape/horizontal) and validates
dimensions via ffprobe after download (rejecting vertical height > width).
Splits long scenes into multiple shots according to style template max shot duration.
"""

import asyncio
import json
import math
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from app.config import (
    COVERR_ENABLED,
    get_coverr_key,
    get_nara_key,
    get_pexels_key,
    get_pixabay_key,
    get_nasa_key,
    STYLE_MAX_SHOT_DURATION,
)
from app.models import ScriptScene


# ── Pixabay cache (24h requirement) ─────────────────────────────────────────

_PIXABAY_CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "jobs" / "_pixabay_cache.json"
_PIXABAY_CACHE_TTL = 86400  # 24 hours in seconds


def _load_pixabay_cache() -> dict:
    if _PIXABAY_CACHE_FILE.exists():
        try:
            data = json.loads(_PIXABAY_CACHE_FILE.read_text(encoding="utf-8"))
            now = time.time()
            return {k: v for k, v in data.items() if now - v.get("_ts", 0) < _PIXABAY_CACHE_TTL}
        except Exception:
            return {}
    return {}


def _save_pixabay_cache(cache: dict) -> None:
    _PIXABAY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PIXABAY_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


# ── FFprobe video dimension validation ──────────────────────────────────────

def _get_video_dimensions(path: str) -> tuple[int, int]:
    """
    Get video width and height using ffprobe.
    Returns (width, height). Returns (0, 0) if failed.
    """
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("x")
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
    except Exception as e:
        print(f"[Footage] ffprobe dimensions check failed for {path}: {e}")
    return 0, 0


def _is_landscape(path: str) -> bool:
    """Return True if width >= height (landscape/horizontal format)."""
    width, height = _get_video_dimensions(path)
    if width > 0 and height > 0:
        if height > width:
            print(f"[Footage] Rejected vertical video: {width}x{height} for {path}")
            return False
    return width > 0 and height > 0 and height <= width


# ── Pexels search ───────────────────────────────────────────────────────────

async def _search_pexels(
    keywords: list[str],
    min_duration: float,
    client: httpx.AsyncClient,
    device_id: str | None = None,
    page: int = 1,
) -> list[dict]:
    """Search Pexels Videos API with orientation=landscape. Returns candidate video list."""
    query = " ".join(keywords[:3])
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": get_pexels_key(device_id=device_id)}
    params = {
        "query": query,
        "per_page": 10,
        "page": page,
        "size": "medium",
        "orientation": "landscape",  # Force horizontal orientation
    }

    results = []
    try:
        resp = await client.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for video in data.get("videos", []):
            files = video.get("video_files", [])
            hd_files = [f for f in files if f.get("height", 0) >= 720 and (f.get("width", 0) >= f.get("height", 0))]
            chosen = hd_files[0] if hd_files else (files[0] if files else None)
            if chosen:
                results.append({
                    "source": "pexels",
                    "provider_asset_id": video.get("id"),
                    "title": video.get("url", "").rstrip("/").split("/")[-1] or "Pexels video",
                    "creator": video.get("user", {}).get("name"),
                    "download_url": chosen["link"],
                    "thumbnail_url": video.get("image"),
                    "width": chosen.get("width"),
                    "height": chosen.get("height"),
                    "duration": video.get("duration", 0),
                    "attribution": f"Video by {video.get('user', {}).get('name', 'Unknown')} from Pexels",
                    "original_url": video.get("url", ""),
                    "rights_status": "valid",
                    "license_name": "Pexels License",
                    "license_url": "https://www.pexels.com/license/",
                })
    except Exception as e:
        print(f"[Footage] Pexels search failed for '{query}': {e}")

    return results


# ── Pixabay search (with caching) ───────────────────────────────────────────

async def _search_pixabay(
    keywords: list[str],
    min_duration: float,
    client: httpx.AsyncClient,
    device_id: str | None = None,
    page: int = 1,
) -> list[dict]:
    """Search Pixabay Videos API with orientation=horizontal and 24h caching."""
    query = " ".join(keywords[:3])
    cache_key = f"video_{query.lower().strip()}_page_{page}"

    cache = _load_pixabay_cache()
    if cache_key in cache:
        print(f"[Footage] Pixabay cache hit for '{query}'")
        cached_list = cache[cache_key].get("results")
        if cached_list:
            return cached_list

    url = "https://pixabay.com/api/videos/"
    params = {
        "key": get_pixabay_key(device_id=device_id),
        "q": query,
        "per_page": 10,
        "page": page,
        "safesearch": "true",
        "orientation": "horizontal",  # Force horizontal orientation
    }

    results = []
    try:
        resp = await client.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for video in data.get("hits", []):
            videos = video.get("videos", {})
            chosen = videos.get("large") or videos.get("medium") or videos.get("small")
            if chosen:
                results.append({
                    "source": "pixabay",
                    "provider_asset_id": video.get("id"),
                    "title": video.get("tags") or "Pixabay video",
                    "creator": video.get("user"),
                    "download_url": chosen["url"],
                    "width": chosen.get("width"),
                    "height": chosen.get("height"),
                    "duration": video.get("duration", 0),
                    "attribution": f"Video by {video.get('user', 'Unknown')} from Pixabay",
                    "pixabay_page": video.get("pageURL", ""),
                    "rights_status": "valid",
                    "license_name": "Pixabay Content License",
                    "license_url": "https://pixabay.com/service/license-summary/",
                })

        cache[cache_key] = {"results": results, "_ts": time.time()}
        _save_pixabay_cache(cache)
    except Exception as e:
        # Do not log exception text: Pixabay includes its API key in the URL.
        print(f"[Footage] Pixabay search failed for '{query}' ({type(e).__name__}).")

    return results


# ── Pixabay Images search (with caching) ───────────────────────────────────

async def _search_pexels_images(
    keywords: list[str],
    client: httpx.AsyncClient,
    device_id: str | None = None,
    page: int = 1,
) -> list[dict]:
    """Search Pexels Photos API with orientation=landscape."""
    query = " ".join(keywords[:3])
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": get_pexels_key(device_id=device_id)}
    params = {
        "query": query,
        "per_page": 10,
        "page": page,
        "orientation": "landscape",
    }
    results = []
    try:
        resp = await client.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for photo in data.get("photos", []):
            src = photo.get("src", {})
            img_url = src.get("large2x") or src.get("large") or src.get("original")
            if img_url:
                results.append({
                    "source": "pexels_image",
                    "provider_asset_id": photo.get("id"),
                    "title": photo.get("alt") or "Pexels photo",
                    "creator": photo.get("photographer"),
                    "download_url": img_url,
                    "thumbnail_url": src.get("medium") or src.get("small"),
                    "width": photo.get("width"),
                    "height": photo.get("height"),
                    "duration": 0,
                    "attribution": f"Photo by {photo.get('photographer', 'Unknown')} from Pexels",
                    "original_url": photo.get("url", ""),
                    "is_image": True,
                    "rights_status": "valid",
                    "license_name": "Pexels License",
                    "license_url": "https://www.pexels.com/license/",
                })
    except Exception as e:
        print(f"[Footage] Pexels image search failed for '{query}': {e}")

    return results


async def _search_pixabay_images(
    keywords: list[str],
    client: httpx.AsyncClient,
    device_id: str | None = None,
    page: int = 1,
) -> list[dict]:
    """Search Pixabay Photos API with orientation=horizontal and 24h caching."""
    query = " ".join(keywords[:3])
    cache_key = f"img_{query.lower().strip()}_page_{page}"

    cache = _load_pixabay_cache()
    if cache_key in cache:
        cached_list = cache[cache_key].get("results")
        if cached_list:
            return cached_list

    url = "https://pixabay.com/api/"
    params = {
        "key": get_pixabay_key(device_id=device_id),
        "q": query,
        "per_page": 10,
        "page": page,
        "safesearch": "true",
        "orientation": "horizontal",
        "image_type": "photo",
    }

    results = []
    try:
        resp = await client.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for hit in data.get("hits", []):
            img_url = hit.get("largeImageURL") or hit.get("webformatURL")
            if img_url:
                results.append({
                    "source": "pixabay_image",
                    "provider_asset_id": hit.get("id"),
                    "title": hit.get("tags") or "Pixabay photo",
                    "creator": hit.get("user"),
                    "download_url": img_url,
                    "thumbnail_url": hit.get("previewURL") or hit.get("webformatURL"),
                    "width": hit.get("imageWidth"),
                    "height": hit.get("imageHeight"),
                    "duration": 0,
                    "attribution": f"Photo by {hit.get('user', 'Unknown')} from Pixabay",
                    "pixabay_page": hit.get("pageURL", ""),
                    "is_image": True,
                    "rights_status": "valid",
                    "license_name": "Pixabay Content License",
                    "license_url": "https://pixabay.com/service/license-summary/",
                })

        cache[cache_key] = {"results": results, "_ts": time.time()}
        _save_pixabay_cache(cache)
    except Exception as e:
        print(f"[Footage] Pixabay image search failed for '{query}' ({type(e).__name__}).")

    return results


# ── License Validation Helper ────────────────────────────────────────────────

def _is_valid_license(extmetadata: dict, license_url_str: str = "") -> bool:
    """Allow only explicit public-domain, CC0, or plain CC-BY licenses."""
    lic_name = str(extmetadata.get("LicenseShortName", {}).get("value", "")).lower()
    lic_url = str(extmetadata.get("LicenseUrl", {}).get("value", "") or license_url_str).lower()
    usage_terms = str(extmetadata.get("UsageTerms", {}).get("value", "")).lower()
    combined = f"{lic_name} {lic_url} {usage_terms}".replace("_", "-")
    if "public domain" in combined or "cc0" in combined or "cc-0" in combined:
        return True
    if "creativecommons.org/licenses/by/" in combined:
        return not any(restriction in combined for restriction in ("-nc", "-nd", "-sa"))
    return False


def _normalize_asset(candidate: dict, keywords: list[str]) -> dict:
    """Attach a provider-neutral asset/rights record to a downloaded candidate."""
    source = str(candidate.get("source", "unknown"))
    provider = source.removesuffix("_image")
    download_url = candidate.get("download_url")
    source_page = (
        candidate.get("source_page_url")
        or candidate.get("original_url")
        or candidate.get("pixabay_page")
        or candidate.get("page_url")
    )
    provider_asset_id = candidate.get("provider_asset_id") or candidate.get("id")
    if not provider_asset_id and download_url:
        import hashlib
        provider_asset_id = hashlib.sha256(str(download_url).encode("utf-8")).hexdigest()[:24]

    default_license = {
        "pexels": ("Pexels License", "https://www.pexels.com/license/"),
        "pixabay": ("Pixabay Content License", "https://pixabay.com/service/license-summary/"),
    }.get(provider, ("غير محدد", None))
    license_name = candidate.get("license_name") or default_license[0]
    license_url = candidate.get("license_url") or default_license[1]
    rights_status = candidate.get("rights_status")
    if not rights_status:
        rights_status = "valid" if provider in {"pexels", "pixabay"} else "needs_review"

    return {
        "provider": provider,
        "providerAssetId": str(provider_asset_id or ""),
        "title": candidate.get("title") or "Media asset",
        "description": candidate.get("description"),
        "thumbnailUrl": candidate.get("thumbnail_url"),
        "previewUrl": candidate.get("preview_url") or candidate.get("thumbnail_url"),
        "downloadUrl": download_url,
        "sourcePageUrl": source_page,
        "width": candidate.get("width"),
        "height": candidate.get("height"),
        "durationSeconds": candidate.get("duration"),
        "fps": candidate.get("fps"),
        "orientation": candidate.get("orientation") or (
            "landscape" if (candidate.get("width") or 0) >= (candidate.get("height") or 0) else "unknown"
        ),
        "creator": candidate.get("creator"),
        "licenseName": license_name,
        "licenseUrl": license_url,
        "rightsStatus": rights_status,
        "requiresAttribution": bool(candidate.get("requires_attribution", provider in {"wikimedia", "archive_org", "loc", "nara", "nasa"})),
        "attributionText": candidate.get("attribution"),
        "isRealHistoricalFootage": bool(candidate.get("is_real_historical_footage", provider in {"archive_org", "loc", "nara"})),
        "isIllustrativeFootage": bool(candidate.get("is_illustrative_footage", provider in {"pexels", "pixabay", "coverr"})),
        "downloadAllowed": bool(download_url and candidate.get("download_allowed", True)),
        "providerMetadata": {
            "keywords": keywords,
            "isImage": bool(candidate.get("is_image", False)),
            "providerTitle": candidate.get("title"),
        },
    }


def _canonical_media_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    except (AttributeError, ValueError):
        pass
    return value.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def _candidate_usage_keys(candidate: dict) -> set[str]:
    """Stable identities used to prevent one source asset appearing twice in a video."""
    source = str(candidate.get("source", candidate.get("provider", "unknown"))).lower()
    keys = set()
    asset_id = candidate.get("provider_asset_id") or candidate.get("id")
    if asset_id is not None:
        keys.add(f"asset:{source}:{asset_id}")

    source_page = (
        candidate.get("source_page_url")
        or candidate.get("original_url")
        or candidate.get("pixabay_page")
        or candidate.get("page_url")
    )
    if source_page:
        keys.add(f"page:{_canonical_media_url(str(source_page))}")
    download_url = candidate.get("download_url")
    if download_url:
        keys.add(f"download:{_canonical_media_url(str(download_url))}")
    return keys


_GENERIC_VISUAL_TERMS = {
    "cinematic", "documentary", "footage", "background", "video", "stock",
    "visual", "scene", "shot", "style", "broll", "b-roll",
}

_SEARCH_STOPWORDS = {
    "about", "after", "again", "against", "along", "also", "among", "and",
    "any", "are", "around", "because", "been", "before", "being", "between",
    "both", "built", "but", "can", "could", "did", "does", "during", "each",
    "even", "every", "from", "further", "had", "has", "have", "here", "hers",
    "him", "his", "how", "into", "its", "just", "like", "many", "might",
    "more", "most", "much", "must", "near", "not", "now", "only", "other",
    "our", "out", "over", "own", "same", "she", "should", "since", "some",
    "such", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "through", "under", "until", "very", "was",
    "were", "what", "when", "where", "which", "while", "who", "will", "with",
    "would", "you", "your", "right", "twenty-five", "billion", "million",
    "thousand", "hundred", "من", "إلى", "على", "في", "عن", "هذا", "هذه",
    "ذلك", "التي", "الذي", "كان", "كانت", "هو", "هي", "ثم", "كما", "لكن",
}


def _scene_search_keywords(scene: ScriptScene, niche_context: str | None = None) -> list[str]:
    supplied = []
    for keyword in scene.visual_keywords:
        cleaned = str(keyword).strip()
        if cleaned and cleaned.lower() not in _GENERIC_VISUAL_TERMS and cleaned.lower() not in supplied:
            supplied.append(cleaned.lower())
    if supplied:
        return supplied[:5]

    narration_terms = re.findall(r"[A-Za-z][A-Za-z'-]{3,}|[\u0600-\u06FF]{4,}", scene.narration.lower())
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for term in narration_terms:
        normalized = term.strip("'-")
        if (
            len(normalized) >= 4
            and normalized not in _SEARCH_STOPWORDS
            and normalized not in _GENERIC_VISUAL_TERMS
        ):
            counts[normalized] = counts.get(normalized, 0) + 1
            first_seen.setdefault(normalized, len(first_seen))
    extracted = sorted(counts, key=lambda term: (-counts[term], first_seen[term]))[:4]

    # Keep niche terms as useful fallbacks, but let repeated story-specific
    # entities (for example, Voyager and heliosphere) lead the search.
    context_terms = re.findall(r"[A-Za-z][A-Za-z'-]{3,}|[\u0600-\u06FF]{4,}", (niche_context or "").lower())
    for term in context_terms:
        normalized = term.strip("'-")
        if (
            len(normalized) >= 4
            and normalized not in _SEARCH_STOPWORDS
            and normalized not in _GENERIC_VISUAL_TERMS
            and normalized not in extracted
        ):
            extracted.append(normalized)
        if len(extracted) >= 5:
            break
    return extracted or ["space", "science"]


def _prioritize_sources(sources: list[str]) -> list[str]:
    """Try configured public/archive sources before commercial stock providers."""
    stock_sources = {"pexels_video", "pixabay_video", "pexels_image", "pixabay_image"}
    unique_sources = list(dict.fromkeys(sources))
    return [source for source in unique_sources if source not in stock_sources] + [
        source for source in unique_sources if source in stock_sources
    ]


async def _search_coverr(
    keywords: list[str],
    client: httpx.AsyncClient,
    device_id: str | None = None,
) -> list[dict]:
    """Coverr adapter; intentionally disabled unless an operator enables it."""
    if not COVERR_ENABLED:
        return []
    try:
        api_key = get_coverr_key(device_id=device_id)
    except RuntimeError:
        return []

    query = " ".join(keywords[:3])
    try:
        response = await client.get(
            "https://api.coverr.co/videos",
            params={"query": query, "page_size": 10, "urls": "true"},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        response.raise_for_status()
        hits = response.json().get("hits", [])
        candidates = []
        for item in hits:
            urls = item.get("urls") or {}
            video_url = urls.get("mp4_download") or urls.get("mp4")
            if not video_url:
                continue
            candidates.append({
                "source": "coverr",
                "provider_asset_id": item.get("id"),
                "title": item.get("title"),
                "description": item.get("description"),
                "download_url": video_url,
                "thumbnail_url": item.get("thumbnail") or item.get("poster"),
                "preview_url": urls.get("mp4_preview"),
                "source_page_url": item.get("url") or "https://coverr.co/",
                "width": item.get("max_width"),
                "height": item.get("max_height"),
                "duration": item.get("duration", 0),
                "attribution": f"Video from Coverr: {item.get('title', 'Coverr video')}",
                "license_name": "Coverr API license (review required)",
                "license_url": "https://coverr.co/license",
                "rights_status": "needs_review",
                "is_image": False,
            })
        return candidates
    except Exception as exc:
        print(f"[Footage] Coverr search failed for '{query}' ({type(exc).__name__}).")
        return []


def _iter_catalog_urls(value):
    """Yield direct media URLs only; catalog record pages are not download URLs."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and key.lower() in {
                "url", "href", "downloadurl", "directurl", "mediaurl", "fileurl", "objecturl",
            }:
                yield item
            else:
                yield from _iter_catalog_urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_catalog_urls(item)


async def _search_nara(
    keywords: list[str],
    client: httpx.AsyncClient,
    device_id: str | None = None,
) -> list[dict]:
    """Search the official NARA Catalog API, accepting only direct media files."""
    try:
        api_key = get_nara_key(device_id=device_id)
    except RuntimeError:
        return []

    query = " ".join(keywords[:3])
    try:
        response = await client.get(
            "https://catalog.archives.gov/api/v2/records/search",
            params={"availableOnline": "true", "q": query},
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=15,
        )
        response.raise_for_status()
        hits = response.json().get("body", {}).get("hits", {}).get("hits", [])
        candidates = []
        for hit in hits:
            source = hit.get("_source", hit)
            media_url = next(
                (
                    url for url in _iter_catalog_urls(source)
                    if url.lower().split("?", 1)[0].endswith(
                        (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".tif", ".tiff")
                    )
                ),
                None,
            )
            if not media_url:
                continue
            source_id = hit.get("_id") or source.get("naId") or source.get("id")
            title = source.get("title") or source.get("objectTitle") or f"NARA record {source_id or ''}"
            candidates.append({
                "source": "nara",
                "provider_asset_id": source_id,
                "title": title,
                "download_url": media_url,
                "source_page_url": f"https://catalog.archives.gov/id/{source_id}" if source_id else "https://catalog.archives.gov/",
                "width": 0,
                "height": 0,
                "duration": 0,
                "attribution": f"National Archives Catalog — {title}",
                "license_name": "NARA record — verify rights for this item",
                "license_url": "https://www.archives.gov/research/catalog",
                "rights_status": "needs_review",
                "is_image": media_url.lower().split("?", 1)[0].endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff")),
            })
            if len(candidates) >= 8:
                break
        return candidates
    except Exception as exc:
        print(f"[Footage] NARA search failed for '{query}' ({type(exc).__name__}).")
        return []


# ── Wikimedia & Archive.org & NASA Media search fallbacks ────────────────────

async def _search_wikimedia(keywords: list[str], client: httpx.AsyncClient) -> list[dict]:
    """Search Wikimedia Commons for public domain/CC landscape images and media."""
    query = " ".join(keywords[:3])
    url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"file:{query} landscape",
        "gsrlimit": 10,
        "prop": "imageinfo",
        "iiprop": "url|mime|dimensions|extmetadata",
    }
    results = []
    accepted_count = 0
    rejected_count = 0
    try:
        resp = await client.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            for pid, page in pages.items():
                imageinfo = page.get("imageinfo", [])
                if not imageinfo:
                    continue
                info = imageinfo[0]
                img_url = info.get("url")
                width = info.get("width", 0)
                height = info.get("height", 0)
                mime = info.get("mime", "")
                ext = info.get("extmetadata", {})
                
                # Mandatory license filtering condition
                lic_valid = _is_valid_license(ext)
                if img_url and (width >= height) and ("image" in mime or "video" in mime):
                    if lic_valid:
                        accepted_count += 1
                        artist = ext.get("Artist", {}).get("value", "Wikimedia Contributor")
                        results.append({
                            "source": "wikimedia",
                            "provider_asset_id": info.get("canonicaltitle") or pid,
                            "title": page.get("title") or "Wikimedia Commons media",
                            "creator": artist,
                            "download_url": img_url,
                            "thumbnail_url": info.get("thumburl"),
                            "width": width,
                            "height": height,
                            "duration": 0,
                            "attribution": f"Media by {artist} from Wikimedia Commons",
                            "source_page_url": f"https://commons.wikimedia.org/?curid={pid}",
                            "license_name": ext.get("LicenseShortName", {}).get("value", "Open license"),
                            "license_url": ext.get("LicenseUrl", {}).get("value"),
                            "rights_status": "valid",
                            "requires_attribution": True,
                            "is_image": not str(mime).lower().startswith("video/"),
                        })
                    else:
                        rejected_count += 1
                        lic_name = ext.get("LicenseShortName", {}).get("value", "Unknown")
                        print(f"[Footage] [Wikimedia REJECTED] '{page.get('title')}' due to non-free license: {lic_name}")
                else:
                    rejected_count += 1
            print(f"[Footage] [Wikimedia License Audit] Query '{query}': {accepted_count} Accepted, {rejected_count} Rejected")
    except Exception as e:
        print(f"[Footage] Wikimedia search failed for '{query}': {e}")
    return results


async def _search_archive_org(keywords: list[str], client: httpx.AsyncClient) -> list[dict]:
    """Search Archive.org for Public Domain / CC movies and images."""
    query = " ".join(keywords[:3])
    url = "https://archive.org/advancedsearch.php"
    params = {
        "q": f"{query} AND (mediatype:movies OR mediatype:image)",
        "fl[]": ["identifier", "title", "mediatype", "licenseurl"],
        "sort[]": "downloads desc",
        "rows": 8,
        "output": "json",
    }
    results = []
    accepted_count = 0
    rejected_count = 0
    try:
        resp = await client.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            docs = resp.json().get("response", {}).get("docs", [])
            for doc in docs:
                identifier = doc.get("identifier")
                lic_url = doc.get("licenseurl", "")
                is_valid = _is_valid_license({}, lic_url)
                if identifier:
                    accepted_count += 1
                    meta_url = f"https://archive.org/metadata/{identifier}"
                    try:
                        m_resp = await client.get(meta_url, timeout=8)
                        if m_resp.status_code == 200:
                            files = m_resp.json().get("files", [])
                            media_entry = next(
                                (
                                    f for f in files
                                    if f.get("name", "").lower().endswith((".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png"))
                                    and f.get("source") != "metadata"
                                ),
                                None,
                            )
                            media_f = media_entry.get("name") if media_entry else None
                            if media_f:
                                is_img = media_f.lower().endswith((".jpg", ".jpeg", ".png"))
                                results.append({
                                    "source": "archive_org",
                                    "provider_asset_id": identifier,
                                    "title": doc.get("title", identifier),
                                    "download_url": f"https://archive.org/download/{identifier}/{quote(media_f, safe='/')}",
                                    "width": int(media_entry.get("width") or 0),
                                    "height": int(media_entry.get("height") or 0),
                                    "duration": float(media_entry.get("length") or 0) if not is_img else 0,
                                    "attribution": f"Media from Archive.org ({doc.get('title', identifier)})",
                                    "source_page_url": f"https://archive.org/details/{identifier}",
                                    "license_name": lic_url or "غير محدد — تحقق من حقوق هذا العنصر",
                                    "license_url": lic_url or None,
                                    "rights_status": "valid" if is_valid else "needs_review",
                                    "requires_attribution": True,
                                    "is_image": is_img,
                                })
                    except Exception:
                        pass
                else:
                    rejected_count += 1
                    print(f"[Footage] [Archive.org REJECTED] '{identifier}' due to non-free license: {lic_url}")
            print(f"[Footage] [Archive.org License Audit] Query '{query}': {accepted_count} Accepted, {rejected_count} Rejected")
    except Exception as e:
        print(f"[Footage] Archive.org search failed for '{query}': {e}")
    return results


async def _search_loc(keywords: list[str], client: httpx.AsyncClient) -> list[dict]:
    """Search Library of Congress (LOC) API for historical media."""
    query = " ".join(keywords[:3])
    url = "https://www.loc.gov/pictures/search/"
    params = {"q": query, "fo": "json", "c": 6}
    results = []
    try:
        resp = await client.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            hits = resp.json().get("results", [])
            for item in hits:
                img_info = item.get("image", {})
                img_url = img_info.get("full") or img_info.get("square")
                if img_url:
                    if img_url.startswith("//"):
                        img_url = "https:" + img_url
                    results.append({
                        "source": "loc",
                        "provider_asset_id": item.get("id"),
                        "title": item.get("title") or "Library of Congress item",
                        "download_url": img_url,
                        "thumbnail_url": img_info.get("thumb"),
                        "width": 0,
                        "height": 0,
                        "duration": 0,
                        "attribution": f"Historical media courtesy of Library of Congress ({item.get('title', 'LOC')})",
                        "source_page_url": item.get("id") or "https://www.loc.gov/",
                        "license_name": "تحقق من شروط استخدام العنصر",
                        "license_url": item.get("rights") or "https://www.loc.gov/legal/",
                        "rights_status": "needs_review",
                        "requires_attribution": True,
                        "is_image": True,
                    })
    except Exception as e:
        print(f"[Footage] LOC search failed for '{query}': {e}")
    return results


async def _search_nasa(keywords: list[str], client: httpx.AsyncClient) -> list[dict]:
    """Search NASA Image and Video Library API for space/science media."""
    query = " ".join(keywords[:3])
    search_url = "https://images-api.nasa.gov/search"

    async def find_assets(media_type: str) -> list[dict]:
        results = []
        response = await client.get(
            search_url,
            params={"q": query, "media_type": media_type, "page_size": 6},
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("collection", {}).get("items", [])[:6]
        for item in items:
            data_info = (item.get("data") or [{}])[0]
            nasa_id = data_info.get("nasa_id")
            if not nasa_id:
                continue
            manifest_response = await client.get(
                f"https://images-api.nasa.gov/asset/{nasa_id}",
                timeout=12,
            )
            if manifest_response.status_code != 200:
                continue
            manifest_urls = manifest_response.json().get("collection", {}).get("items", [])
            allowed_exts = (".mp4", ".mov", ".webm") if media_type == "video" else (".jpg", ".jpeg", ".png", ".tif", ".tiff")
            asset_url = next(
                (
                    value for value in manifest_urls
                    if isinstance(value, str)
                    and value.lower().split("?", 1)[0].endswith(allowed_exts)
                ),
                None,
            )
            if not asset_url:
                continue
            preview = next(
                (
                    link.get("href") for link in item.get("links", [])
                    if link.get("rel") == "preview" and link.get("href")
                ),
                None,
            )
            title = data_info.get("title", "NASA media")
            results.append({
                "source": "nasa",
                "provider_asset_id": nasa_id,
                "title": title,
                "description": data_info.get("description"),
                "download_url": asset_url,
                "thumbnail_url": preview,
                "preview_url": preview,
                "source_page_url": item.get("href") or "https://images.nasa.gov/",
                "width": 0,
                "height": 0,
                "duration": data_info.get("duration", 0),
                "attribution": f"NASA — {title}",
                "license_name": "NASA media — verify rights and third-party credits",
                "license_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
                "rights_status": "needs_review",
                "is_image": media_type == "image",
            })
        return results

    try:
        videos = await find_assets("video")
        return videos if videos else await find_assets("image")
    except Exception as exc:
        print(f"[Footage] NASA search failed for '{query}' ({type(exc).__name__}).")
        return []


async def _search_nasa_apod(client: httpx.AsyncClient, device_id: str | None = None) -> list[dict]:
    """Fetch NASA APOD (Astronomy Picture of the Day)."""
    import os
    try:
        nasa_key = get_nasa_key(device_id=device_id)
    except RuntimeError:
        nasa_key = "DEMO_KEY"
    url = "https://api.nasa.gov/planetary/apod"
    params = {"api_key": nasa_key}
    results = []
    try:
        resp = await client.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            img_url = data.get("url")
            if img_url and data.get("media_type") == "image":
                results.append({
                    "source": "nasa_apod",
                    "download_url": img_url,
                    "width": 1920,
                    "height": 1080,
                    "duration": 0,
                    "attribution": f"NASA APOD: {data.get('title', 'Space Image')}",
                    "is_image": True,
                })
    except Exception as e:
        print(f"[Footage] NASA APOD failed ({type(e).__name__}).")
    return results


# ── Download file to local disk with Retries & User-Agent ────────────────────

async def _download_video(url: str, output_path: str, client: httpx.AsyncClient, max_retries: int = 3) -> None:
    """Download a video/image file to local disk with automatic retry on network drops."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }
    
    last_err = None
    for attempt in range(1, min(max_retries, 3) + 1):
        try:
            async with client.stream("GET", url, headers=headers, timeout=90, follow_redirects=True) as resp:
                resp.raise_for_status()
                content_length = int(resp.headers.get("content-length", "0") or 0)
                if content_length > 350 * 1024 * 1024:
                    raise RuntimeError("Downloaded media exceeds the 350 MB safety limit.")
                total = 0
                with open(output_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=16384):
                        total += len(chunk)
                        if total > 350 * 1024 * 1024:
                            raise RuntimeError("Downloaded media exceeds the 350 MB safety limit.")
                        f.write(chunk)
            print(f"[Footage] Downloaded to {output_path} (attempt {attempt})")
            return
        except Exception as e:
            last_err = e
            host = urlsplit(url).netloc or "unknown host"
            print(f"[Footage] Download attempt {attempt}/{max_retries} failed on {host} ({type(e).__name__}).")
            # Clean incomplete file
            if Path(output_path).exists():
                try:
                    Path(output_path).unlink()
                except Exception:
                    pass
            if attempt < max_retries:
                await asyncio.sleep( attempt * 1.5 )
                
    raise RuntimeError(f"Failed to download footage after {max_retries} attempts: {last_err}")


# ── Fetch single clip/image with landscape verification ────────────────────

async def search_video_sources(
    keywords: list[str],
    source_ids: list[str] | None = None,
    device_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search selected providers and return normalized source/license metadata."""
    searchers = {
        "pexels_video": lambda client: _search_pexels(keywords, 0, client, device_id),
        "pixabay_video": lambda client: _search_pixabay(keywords, 0, client, device_id),
        "pexels_image": lambda client: _search_pexels_images(keywords, client, device_id),
        "pixabay_image": lambda client: _search_pixabay_images(keywords, client, device_id),
        "coverr_video": lambda client: _search_coverr(keywords, client, device_id),
        "nara": lambda client: _search_nara(keywords, client, device_id),
        "nasa": lambda client: _search_nasa(keywords, client),
        "wikimedia": lambda client: _search_wikimedia(keywords, client),
        "archive_org": lambda client: _search_archive_org(keywords, client),
        "loc": lambda client: _search_loc(keywords, client),
    }
    selected = source_ids or [
        "archive_org", "loc", "wikimedia", "nasa", "nara",
        "pixabay_video", "pexels_video", "pixabay_image", "pexels_image",
    ]
    output = []
    async with httpx.AsyncClient(timeout=18) as client:
        for source_id in selected:
            searcher = searchers.get(source_id)
            if not searcher:
                continue
            try:
                candidates = await searcher(client)
            except Exception as exc:
                print(f"[Footage] Search endpoint source '{source_id}' failed ({type(exc).__name__}).")
                continue
            for candidate in candidates:
                asset = _normalize_asset(candidate, keywords)
                asset["provider"] = candidate.get("source", asset["provider"]).removesuffix("_image")
                output.append(asset)
                if len(output) >= limit:
                    return output
    return output


async def fetch_footage_for_shot(
    keywords: list[str],
    min_duration: float,
    output_path: str,
    attr_path: str,
    client: httpx.AsyncClient,
    used_urls: set[str],
    source_priority: list[str] | None = None,
    device_id: str | None = None,
) -> str:
    """Fetch one validated horizontal asset using the niche's source priority."""
    searchers = {
        "pexels_video": lambda page=1: _search_pexels(keywords, min_duration, client, device_id, page),
        "pixabay_video": lambda page=1: _search_pixabay(keywords, min_duration, client, device_id, page),
        "pexels_image": lambda page=1: _search_pexels_images(keywords, client, device_id, page),
        "pixabay_image": lambda page=1: _search_pixabay_images(keywords, client, device_id, page),
        "coverr_video": lambda page=1: _search_coverr(keywords, client, device_id),
        "nara": lambda page=1: _search_nara(keywords, client, device_id),
        "wikimedia": lambda page=1: _search_wikimedia(keywords, client),
        "archive_org": lambda page=1: _search_archive_org(keywords, client),
        "loc": lambda page=1: _search_loc(keywords, client),
        "nasa": lambda page=1: _search_nasa(keywords, client),
        "nasa_apod": lambda page=1: _search_nasa_apod(client, device_id),
    }
    priority = list(source_priority or [
        "archive_org", "loc", "wikimedia", "nasa", "nara",
        "pixabay_video", "pexels_video", "pixabay_image", "pexels_image",
    ])
    # Preserve the user's ordering among archival/public sources, while using
    # commercial stock providers only after those sources have been tried.
    priority = _prioritize_sources(priority)
    chosen = None
    final_output_path = output_path
    attempts_made = 0
    paginated_sources = {"pexels_video", "pixabay_video", "pexels_image", "pixabay_image"}

    for source in priority:
        searcher = searchers.get(source)
        if not searcher:
            continue
        page_numbers = range(1, 4) if source in paginated_sources else (1,)
        for page_number in page_numbers:
            attempts_made += 1
            try:
                candidates = await searcher(page_number)
            except Exception as exc:
                print(f"[Footage] Source '{source}' unavailable ({type(exc).__name__}).")
                continue

            # Never fall back to an asset already used elsewhere in this video.
            unused_candidates = [
                candidate for candidate in candidates
                if candidate.get("download_url")
                and not (_candidate_usage_keys(candidate) & used_urls)
            ]
            for candidate in unused_candidates:
                target_path = output_path
                if candidate.get("is_image", False) and not target_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    target_path = str(Path(output_path).with_suffix(".jpg"))
                try:
                    await _download_video(candidate["download_url"], target_path, client)
                    if not _is_landscape(target_path):
                        print(f"[Footage] Rejected non-landscape or unprobeable asset from '{source}'.")
                        Path(target_path).unlink(missing_ok=True)
                        continue
                    chosen = candidate
                    final_output_path = target_path
                    used_urls.update(_candidate_usage_keys(candidate))
                    print(
                        f"[Footage] Selected '{source}' "
                        f"({'IMAGE' if candidate.get('is_image') else 'VIDEO'}) for keywords={keywords}"
                    )
                    break
                except Exception as download_error:
                    print(f"[Footage] Candidate from '{source}' failed ({type(download_error).__name__}); trying next.")
                    Path(target_path).unlink(missing_ok=True)
            if chosen:
                break
        if chosen:
            break

    if not chosen:
        raise RuntimeError(
            f"No configured video source returned usable media for keywords: {keywords} "
            f"({attempts_made} search attempt(s); repeated assets are not reused)"
        )

    metadata = _normalize_asset(chosen, keywords)
    # Download links may be short-lived or signed; keep them out of review metadata.
    metadata["downloadUrl"] = None
    attr_data = {
        **metadata,
        "source": chosen["source"],
        "attribution": chosen["attribution"],
        "keywords": keywords,
        "is_image": chosen.get("is_image", False),
    }
    Path(attr_path).write_text(json.dumps(attr_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return final_output_path



# ── All scenes & shots fetching ─────────────────────────────────────────────

async def fetch_all_footage(
    scenes: list[ScriptScene],
    audio_durations: list[float],
    job_dir: Path,
    style: str = "documentary",
    niche_id: str | None = None,
    device_id: str | None = None,
    source_priority: list[str] | None = None,
) -> list[list[str]]:
    """
    Fetch footage for all scenes, splitting long scenes into multiple shots
    based on style's MAX_SHOT_DURATION.
    Returns list of list of clip paths per scene: [[s0_shot0, s0_shot1], [s1_shot0], ...]
    """
    clips_dir = job_dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    max_shot_dur = STYLE_MAX_SHOT_DURATION.get(style, 10.0)
    all_scene_shots: list[list[str]] = []
    used_urls: set[str] = set()
    resolved_priority = source_priority
    niche_context = niche_id or ""
    if resolved_priority is None and niche_id:
        from app.niches import find_niche_info
        niche_info = find_niche_info(niche_id)
        if niche_info:
            resolved_priority = [item.strip() for item in niche_info.get("sources", "").split(",") if item.strip()]
            niche_context = str(niche_info.get("title") or niche_id)

    async with httpx.AsyncClient() as client:
        for scene_idx, scene in enumerate(scenes):
            scene_duration = audio_durations[scene_idx]
            # Calculate how many shots needed for this scene
            num_shots = max(1, math.ceil(scene_duration / max_shot_dur))
            shot_target_dur = scene_duration / num_shots

            print(
                f"[Footage] Scene {scene_idx} ({scene_duration:.2f}s, style='{style}') "
                f"split into {num_shots} shot(s) (~{shot_target_dur:.2f}s each)"
            )

            scene_clip_paths = []
            for shot_idx in range(num_shots):
                output = str(clips_dir / f"scene_{scene_idx}_shot_{shot_idx}.mp4")
                attr = str(clips_dir / f"scene_{scene_idx}_shot_{shot_idx}_attr.json")

                # Keyword variations per shot for variety
                kw = _scene_search_keywords(scene, niche_context)
                if shot_idx > 0 and len(kw) > 1:
                    # Rotate or modify keywords slightly for different shots
                    kw = kw[shot_idx % len(kw):] + kw[:shot_idx % len(kw)]

                resolved_output = await fetch_footage_for_shot(
                    keywords=kw,
                    min_duration=shot_target_dur,
                    output_path=output,
                    attr_path=attr,
                    client=client,
                    used_urls=used_urls,
                    source_priority=resolved_priority,
                    device_id=device_id,
                )
                scene_clip_paths.append(resolved_output)

                if shot_idx < num_shots - 1:
                    await asyncio.sleep(0.3)

            all_scene_shots.append(scene_clip_paths)

            if scene_idx < len(scenes) - 1:
                await asyncio.sleep(0.5)

    print(f"[Footage] Fetched footage for {len(scenes)} scenes (total shots: {sum(len(s) for s in all_scene_shots)})")
    return all_scene_shots

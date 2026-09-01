"""
imaginAI — Stage 3: Stock Footage Retrieval
Searches Pexels (primary) then Pixabay (fallback) for video clips.
Downloads locally — no hotlinking.
Filters for horizontal videos (orientation=landscape/horizontal) and validates
dimensions via ffprobe after download (rejecting vertical height > width).
Splits long scenes into multiple shots according to style template max shot duration.
"""

import asyncio
import json
import math
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import get_pexels_key, get_pixabay_key, get_nasa_key, STYLE_MAX_SHOT_DURATION
from app.models import ScriptScene


DEFAULT_SOURCE_PRIORITY = ["pexels_video", "pixabay_video"]

_TOP10_SOURCE_HINTS = {
    "space": ["nasa_image_library", "nasa_apod", "pexels_video", "pixabay_video"],
    "planet": ["nasa_image_library", "nasa_apod", "pexels_video", "pixabay_video"],
    "galaxy": ["nasa_image_library", "nasa_apod", "pexels_video", "pixabay_video"],
    "animal": ["pexels_video", "pixabay_video"],
    "wildlife": ["pexels_video", "pixabay_video"],
    "ocean": ["pexels_video", "pixabay_video"],
    "underwater": ["pexels_video", "pixabay_video"],
    "sea": ["pexels_video", "pixabay_video"],
    "history": ["wikimedia", "archive_org", "loc", "pexels_video", "pixabay_video"],
    "historical": ["wikimedia", "archive_org", "loc", "pexels_video", "pixabay_video"],
    "ancient": ["wikimedia", "archive_org", "loc", "pexels_video", "pixabay_video"],
    "abandoned": ["pexels_video", "pixabay_video"],
    "ruins": ["pexels_video", "pixabay_video"],
    "crime": ["archive_org", "wikimedia", "pexels_video", "pixabay_video"],
    "religion": ["wikimedia", "pexels_video", "pixabay_video"],
}


def _top10_source_priority(keywords: list[str]) -> list[str]:
    """Infer the original topic niche for Top 10 footage when keywords reveal it."""
    text = " ".join(keywords).lower()
    for hint, priority in _TOP10_SOURCE_HINTS.items():
        if hint in text:
            return list(priority)
    return list(DEFAULT_SOURCE_PRIORITY)


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


def _is_valid_download_url(url: object) -> bool:
    """Reject missing, malformed, or non-HTTP(S) media URLs before downloading."""
    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _probe_video_file(path: str) -> tuple[int, int, float] | None:
    """Return (width, height, duration) only for a valid video stream."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_type,width,height:format=duration",
                "-of", "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        if stream.get("codec_type") != "video":
            return None
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration = float((data.get("format") or {}).get("duration") or 0)
        if width <= 0 or height <= 0 or duration <= 0:
            return None
        return width, height, duration
    except Exception as exc:
        print(f"[Footage] Video validation failed for {path}: {exc}")
        return None


def _is_valid_image_file(path: str) -> bool:
    """Validate an image using its actual file signature/MIME type."""
    try:
        result = subprocess.run(
            ["file", "--brief", "--mime-type", path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip().lower().startswith("image/")
    except Exception as exc:
        print(f"[Footage] Image validation failed for {path}: {exc}")
        return False


def _validate_media_file(path: str, is_image: bool) -> bool:
    """Require a non-empty readable image or a probeable video with valid dimensions."""
    media_path = Path(path)
    if not media_path.is_file() or media_path.stat().st_size <= 0:
        return False
    if is_image:
        return _is_valid_image_file(path)
    probed = _probe_video_file(path)
    if not probed:
        return False
    width, height, _duration = probed
    if height > width:
        print(f"[Footage] Rejected vertical video: {width}x{height} for {path}")
        return False
    return True


# ── Pexels search ───────────────────────────────────────────────────────────

async def _search_pexels(keywords: list[str], min_duration: float, client: httpx.AsyncClient, device_id: str | None = None) -> list[dict]:
    """Search Pexels Videos API with orientation=landscape. Returns candidate video list."""
    query = " ".join(keywords[:3])
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": get_pexels_key(device_id=device_id)}
    params = {
        "query": query,
        "per_page": 10,
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
                    "source": "pexels_video",
                    "download_url": chosen["link"],
                    "width": chosen.get("width"),
                    "height": chosen.get("height"),
                    "duration": video.get("duration", 0),
                    "attribution": f"Video by {video.get('user', {}).get('name', 'Unknown')} from Pexels",
                    "original_url": video.get("url", ""),
                })
    except Exception as e:
        print(f"[Footage] Pexels search failed for '{query}': {e}")

    return results


# ── Pixabay search (with caching) ───────────────────────────────────────────

async def _search_pixabay(keywords: list[str], min_duration: float, client: httpx.AsyncClient, device_id: str | None = None) -> list[dict]:
    """Search Pixabay Videos API with orientation=horizontal and 24h caching."""
    query = " ".join(keywords[:3])
    cache_key = query.lower().strip()

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
                    "source": "pixabay_video",
                    "download_url": chosen["url"],
                    "width": chosen.get("width"),
                    "height": chosen.get("height"),
                    "duration": video.get("duration", 0),
                    "attribution": f"Video by {video.get('user', 'Unknown')} from Pixabay",
                    "pixabay_page": video.get("pageURL", ""),
                })

        cache[cache_key] = {"results": results, "_ts": time.time()}
        _save_pixabay_cache(cache)
    except Exception as e:
        print(f"[Footage] Pixabay search failed for '{query}': {e}")

    return results


# ── Pixabay Images search (with caching) ───────────────────────────────────

async def _search_pexels_images(keywords: list[str], client: httpx.AsyncClient, device_id: str | None = None) -> list[dict]:
    """Search Pexels Photos API with orientation=landscape."""
    query = " ".join(keywords[:3])
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": get_pexels_key(device_id=device_id)}
    params = {
        "query": query,
        "per_page": 10,
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
                    "download_url": img_url,
                    "width": photo.get("width"),
                    "height": photo.get("height"),
                    "duration": 0,
                    "attribution": f"Photo by {photo.get('photographer', 'Unknown')} from Pexels",
                    "original_url": photo.get("url", ""),
                    "is_image": True,
                })
    except Exception as e:
        print(f"[Footage] Pexels image search failed for '{query}': {e}")

    return results


async def _search_pixabay_images(keywords: list[str], client: httpx.AsyncClient, device_id: str | None = None) -> list[dict]:
    """Search Pixabay Photos API with orientation=horizontal and 24h caching."""
    query = " ".join(keywords[:3])
    cache_key = f"img_{query.lower().strip()}"

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
                    "download_url": img_url,
                    "width": hit.get("imageWidth"),
                    "height": hit.get("imageHeight"),
                    "duration": 0,
                    "attribution": f"Photo by {hit.get('user', 'Unknown')} from Pixabay",
                    "pixabay_page": hit.get("pageURL", ""),
                    "is_image": True,
                })

        cache[cache_key] = {"results": results, "_ts": time.time()}
        _save_pixabay_cache(cache)
    except Exception as e:
        print(f"[Footage] Pixabay image search failed for '{query}': {e}")

    return results


# ── License Validation Helper ────────────────────────────────────────────────

def _is_valid_license(extmetadata: dict, license_url_str: str = "") -> bool:
    """Return True if license is Public Domain, CC0, CC-BY, or open license."""
    lic_name = str(extmetadata.get("LicenseShortName", {}).get("value", "")).lower()
    lic_url = str(extmetadata.get("LicenseUrl", {}).get("value", "") or license_url_str).lower()
    usage_terms = str(extmetadata.get("UsageTerms", {}).get("value", "")).lower()
    combined = f"{lic_name} {lic_url} {usage_terms}"
    
    valid_keywords = [
        "public domain", "pd", "cc0", "cc-0", "cc by", "cc-by",
        "creativecommons.org/publicdomain", "creativecommons.org/licenses/by",
        "gfdl", "free artwork", "attribution"
    ]
    return any(kw in combined for kw in valid_keywords)


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
                is_image = "video" not in mime.lower()
                
                # Mandatory license filtering condition
                lic_valid = _is_valid_license(ext)
                if img_url and (width >= height) and ("image" in mime or "video" in mime):
                    if lic_valid:
                        accepted_count += 1
                        artist = ext.get("Artist", {}).get("value", "Wikimedia Contributor")
                        results.append({
                            "source": "wikimedia",
                            "download_url": img_url,
                            "width": width,
                            "height": height,
                            "duration": 0,
                            "attribution": f"Media by {artist} from Wikimedia Commons",
                            "is_image": is_image,
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
                is_valid = _is_valid_license({}, lic_url) or ("publicdomain" in lic_url.lower()) or not lic_url
                if is_valid and identifier:
                    accepted_count += 1
                    meta_url = f"https://archive.org/metadata/{identifier}"
                    try:
                        m_resp = await client.get(meta_url, timeout=8)
                        if m_resp.status_code == 200:
                            files = m_resp.json().get("files", [])
                            media_f = next((f["name"] for f in files if f.get("name", "").endswith((".mp4", ".jpg", ".jpeg"))), None)
                            if media_f:
                                is_img = not media_f.endswith(".mp4")
                                results.append({
                                    "source": "archive_org",
                                    "download_url": f"https://archive.org/download/{identifier}/{media_f}",
                                    "width": 1920,
                                    "height": 1080,
                                    "duration": 0 if is_img else 10,
                                    "attribution": f"Media from Archive.org ({doc.get('title', identifier)})",
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
                        "download_url": img_url,
                        "width": 1920,
                        "height": 1080,
                        "duration": 0,
                        "attribution": f"Historical media courtesy of Library of Congress ({item.get('title', 'LOC')})",
                        "is_image": True,
                    })
    except Exception as e:
        print(f"[Footage] LOC search failed for '{query}': {e}")
    return results


async def _search_nasa(keywords: list[str], client: httpx.AsyncClient) -> list[dict]:
    """Search NASA Image and Video Library API for space/science media."""
    query = " ".join(keywords[:3])
    url = "https://images-api.nasa.gov/search"
    params = {
        "q": query,
        "media_type": "image",
    }
    results = []
    try:
        resp = await client.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("collection", {}).get("items", [])[:6]
            for item in items:
                links = item.get("links", [])
                data_info = item.get("data", [{}])[0]
                img_url = None
                for link in links:
                    if link.get("rel") == "preview" or link.get("render") == "image":
                        img_url = link.get("href")
                        break
                if img_url:
                    results.append({
                        "source": "nasa_image_library",
                        "download_url": img_url,
                        "width": 1920,
                        "height": 1080,
                        "duration": 0,
                        "attribution": f"Media courtesy of NASA ({data_info.get('title', 'Public Domain')})",
                        "is_image": True,
                    })
    except Exception as e:
        print(f"[Footage] NASA search failed for '{query}': {e}")
    return results


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
        print(f"[Footage] NASA APOD failed: {e}")
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
                if total <= 0 or not Path(output_path).is_file() or Path(output_path).stat().st_size <= 0:
                    raise RuntimeError("Downloaded media is empty.")
            print(f"[Footage] Downloaded to {output_path} (attempt {attempt})")
            return
        except Exception as e:
            last_err = e
            print(f"[Footage] Download attempt {attempt}/{max_retries} failed for {url}: {e}")
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

async def fetch_footage_for_shot(
    keywords: list[str],
    min_duration: float,
    output_path: str,
    attr_path: str,
    client: httpx.AsyncClient,
    used_urls: set[str],
    source_priority: list[str] | None = None,
    device_id: str | None = None,
    niche_id: str | None = None,
) -> str:
    """Fetch one validated horizontal asset using the niche's source priority."""
    searchers = {
        "pexels_video": lambda: _search_pexels(keywords, min_duration, client, device_id),
        "pixabay_video": lambda: _search_pixabay(keywords, min_duration, client, device_id),
        "pexels_image": lambda: _search_pexels_images(keywords, client, device_id),
        "pixabay_image": lambda: _search_pixabay_images(keywords, client, device_id),
        "wikimedia": lambda: _search_wikimedia(keywords, client),
        "archive_org": lambda: _search_archive_org(keywords, client),
        "loc": lambda: _search_loc(keywords, client),
        "nasa_image_library": lambda: _search_nasa(keywords, client),
        "nasa": lambda: _search_nasa(keywords, client),
        "nasa_apod": lambda: _search_nasa_apod(client, device_id),
    }
    if niche_id in {"top_10", "توب 10 (أي مجال)"}:
        priority = _top10_source_priority(keywords)
    elif source_priority:
        priority = source_priority
    else:
        priority = list(DEFAULT_SOURCE_PRIORITY)

    # Search and validate each source in order. A source is not successful
    # until at least one of its downloaded candidates passes media validation.
    attempted_sources: list[str] = []
    for source in priority:
        searcher = searchers.get(source)
        if not searcher:
            print(f"[Footage] Unknown source '{source}', skipping.")
            continue
        attempted_sources.append(source)
        try:
            candidates = await searcher()
        except Exception as exc:
            print(f"[Footage] Source '{source}' unavailable: {exc}")
            continue
        if not candidates:
            print(f"[Footage] Source '{source}' returned no candidates; trying next source.")
            continue

        ordered_candidates = [
            c for c in candidates
            if _is_valid_download_url(c.get("download_url")) and c["download_url"] not in used_urls
        ] + [
            c for c in candidates
            if _is_valid_download_url(c.get("download_url")) and c["download_url"] in used_urls
        ]

        for cand in ordered_candidates:
            is_image = bool(cand.get("is_image", False))
            target_path = output_path
            if is_image and not target_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                target_path = str(Path(output_path).with_suffix(".jpg"))

            try:
                await _download_video(cand["download_url"], target_path, client)
                if not _validate_media_file(target_path, is_image):
                    print(
                        f"[Footage] Rejected invalid "
                        f"{'image' if is_image else 'video'} from '{source}'; trying next candidate."
                    )
                    Path(target_path).unlink(missing_ok=True)
                    continue

                used_urls.add(cand["download_url"])
                attr_data = {
                    "source": cand.get("source", source),
                    "attribution": cand.get("attribution", ""),
                    "keywords": keywords,
                    "is_image": is_image,
                }
                Path(attr_path).write_text(
                    json.dumps(attr_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(
                    f"[Footage] [Shot Source Resolution] Source='{cand.get('source', source)}' "
                    f"({'IMAGE' if is_image else 'VIDEO'}) for keywords={keywords}"
                )
                return target_path
            except Exception as dl_err:
                print(
                    f"[Footage] Candidate from '{source}' failed validation/download: "
                    f"{dl_err}. Trying next candidate..."
                )
                Path(target_path).unlink(missing_ok=True)

        print(f"[Footage] All candidates from '{source}' failed; trying next source.")

    raise RuntimeError(
        f"All configured footage sources failed ({', '.join(attempted_sources) or 'none'}) "
        f"for keywords: {keywords}"
    )



# ── All scenes & shots fetching ─────────────────────────────────────────────

async def fetch_all_footage(
    scenes: list[ScriptScene],
    audio_durations: list[float],
    job_dir: Path,
    style: str = "documentary",
    niche_id: str | None = None,
    device_id: str | None = None,
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
    source_priority = None
    if niche_id:
        from app.niches import get_niche_source_priority
        source_priority = get_niche_source_priority(niche_id)

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
                kw = scene.visual_keywords.copy()
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
                    source_priority=source_priority,
                    device_id=device_id,
                    niche_id=niche_id,
                )
                scene_clip_paths.append(resolved_output)

                if shot_idx < num_shots - 1:
                    await asyncio.sleep(0.3)

            all_scene_shots.append(scene_clip_paths)

            if scene_idx < len(scenes) - 1:
                await asyncio.sleep(0.5)

    print(f"[Footage] Fetched footage for {len(scenes)} scenes (total shots: {sum(len(s) for s in all_scene_shots)})")
    return all_scene_shots

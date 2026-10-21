import json
import uuid
import shutil
import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request, Response, Header, Depends, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import (
    COVERR_ENABLED,
    JOBS_DIR,
    STORAGE_DIR,
    VOICE_PROVIDERS,
    get_coverr_key,
    get_nara_key,
    get_pexels_key,
    get_pixabay_key,
)
from app.models import (
    JobRequest,
    JobStatusResponse,
    ProjectCreate,
    ProjectRename,
    ProjectVoiceRename,
    ProjectResponse,
    VideoResponse,
    ResumeJobRequest,
    SectionApprovalRequest,
    make_initial_status,
    ApiKeySaveRequest,
    ApiKeyTestRequest,
    VideoSourceSearchRequest,
)
from app.api_keys import (
    delete_user_key,
    get_user_key_by_id,
    list_key_metadata,
    record_test_result,
    save_user_key,
)
from app.db import (
    init_db,
    get_db_connection,
    get_or_create_device,
    clear_device_data,
    create_project,
    get_projects_by_device,
    get_project,
    rename_project,
    delete_project,
    get_project_voice_settings,
    rename_project_voice,
    hide_project_voice,
    create_video,
    update_video,
    get_videos_by_device,
    get_videos_by_project,
    create_custom_voice,
    get_custom_voices_by_device,
    create_custom_font,
    get_custom_fonts_by_device,
    get_custom_font_by_id,
)
from app.services.rate_limit import check_and_increment_rate_limits, DATA_DIR

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required in the runtime.")
    # Jobs cannot continue executing after a process restart. Mark only jobs
    # that were actively running as interrupted; review/approval states remain
    # resumable and are intentionally left untouched.
    for job_path in JOBS_DIR.iterdir() if JOBS_DIR.exists() else []:
        status_path = job_path / "status.json"
        if not status_path.exists():
            continue
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("status") in {"pending", "processing"}:
                stages = status.get("stages", {})
                can_resume_render = (
                    status.get("status") == "processing"
                    and status.get("current_stage") in {"captions", "render"}
                    and stages.get("footage") == "done"
                    and (job_path / "review_data.json").is_file()
                )
                if can_resume_render:
                    status.update({
                        "status": "pending_review",
                        "current_stage": "pending_review",
                        "error": "توقف الرندر بعد إعادة تشغيل الخادم. اللقطات محفوظة ويمكنك مراجعتها ثم إعادة المحاولة.",
                    })
                    from app.db import update_video
                    update_video(video_id=job_path.name, status="pending_review")
                else:
                    status.update({
                        "status": "failed",
                        "current_stage": None,
                        "error": "توقفت عملية التوليد بسبب إعادة تشغيل الخادم. يرجى بدء توليد جديد.",
                    })
                    from app.db import update_video
                    update_video(video_id=job_path.name, status="failed")
                status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            continue
    yield

app = FastAPI(title="imaginAI", version="1.0.0", lifespan=lifespan)


# ── CORS (allow Next.js dev server) ─────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Device Identity Dependency ──────────────────────────────────────────────
COOKIE_NAME = "imaginai_device_id"
COOKIE_MAX_AGE = 31536000  # 1 year (365 days)


def get_current_device(request: Request, response: Response) -> str:
    """
    Extract device_id from cookie 'imaginai_device_id', header 'x-device-id', or header 'x-visitor-id'.
    If missing, generate a new UUID, record in SQLite, and set long-term cookie.
    """
    device_id = (
        request.headers.get("x-device-id")
        or request.headers.get("x-visitor-id")
        or request.cookies.get(COOKIE_NAME)
    )

    if not device_id:
        device_id = f"dev_{uuid.uuid4().hex}"

    if request.cookies.get(COOKIE_NAME) != device_id:
        response.set_cookie(
            key=COOKIE_NAME,
            value=device_id,
            max_age=COOKIE_MAX_AGE,
            httponly=False,
            samesite="lax",
            secure=False,
        )

    get_or_create_device(device_id)
    return device_id


# ── Helpers ──────────────────────────────────────────────────────────────────

def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _read_status(job_id: str) -> dict:
    status_file = _job_dir(job_id) / "status.json"
    if not status_file.exists():
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return json.loads(status_file.read_text(encoding="utf-8"))


def _write_status(job_id: str, data: dict) -> None:
    status_file = _job_dir(job_id) / "status.json"
    status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _assert_job_owner(job_id: str, device_id: str) -> None:
    """Prevent one device from reading or mutating another device's job."""
    request_file = _job_dir(job_id) / "request.json"
    if request_file.exists():
        try:
            owner = json.loads(request_file.read_text(encoding="utf-8")).get("device_id")
            if owner and owner != device_id:
                raise HTTPException(status_code=404, detail="Job not found")
            return
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=404, detail="Job not found")

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT p.device_id FROM videos v
            JOIN projects p ON p.project_id = v.project_id
            WHERE v.video_id = ?
            """,
            (job_id,),
        ).fetchone()
    if not row or row["device_id"] != device_id:
        raise HTTPException(status_code=404, detail="Job not found")


def _assert_project_owner(project_id: str, device_id: str) -> None:
    project = get_project(project_id)
    if not project or project["device_id"] != device_id:
        raise HTTPException(status_code=404, detail="Project not found")


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/niches")
async def get_niches():
    """Return catalog of domains and sub-niches."""
    from app.niches import get_all_niches
    return {"domains": get_all_niches()}


# ── Device & Project Routes ──────────────────────────────────────────────────

@app.get("/api/device/me")
async def get_device_info(device_id: str = Depends(get_current_device)):
    """Return device identity info."""
    return get_or_create_device(device_id)


@app.delete("/api/device/reset")
async def reset_device(response: Response, device_id: str = Depends(get_current_device)):
    """Clear all projects, videos, and data for the current device and delete identity cookie."""
    clear_device_data(device_id)
    response.delete_cookie(COOKIE_NAME)
    return {"status": "ok", "message": "تم مسح جميع بيانات هذا الجهاز بنجاح."}


@app.post("/api/projects")
async def api_create_project(req: ProjectCreate, device_id: str = Depends(get_current_device)):
    """Create a new project linked to device_id."""
    from app.niches import find_niche_info
    if not find_niche_info(req.niche) and not req.niche.strip():
        raise HTTPException(status_code=400, detail="يرجى اختيار نيتش صالح.")
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    return create_project(
        project_id=project_id,
        device_id=device_id,
        domain=req.domain,
        niche=req.niche,
        name=req.name,
    )


@app.get("/api/projects")
async def api_list_projects(device_id: str = Depends(get_current_device)):
    """List all projects for the current device."""
    return {"projects": get_projects_by_device(device_id)}


@app.get("/api/projects/{project_id}")
async def api_get_project(project_id: str, device_id: str = Depends(get_current_device)):
    """Get project details and its videos."""
    proj = get_project(project_id)
    _assert_project_owner(project_id, device_id)
    videos = get_videos_by_project(project_id)
    return {"project": proj, "videos": videos}


@app.patch("/api/projects/{project_id}")
async def api_rename_project(
    project_id: str,
    req: ProjectRename,
    device_id: str = Depends(get_current_device),
):
    _assert_project_owner(project_id, device_id)
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="يرجى إدخال اسم صالح للمشروع.")
    return rename_project(project_id, name)


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str, device_id: str = Depends(get_current_device)):
    project = get_project(project_id)
    _assert_project_owner(project_id, device_id)
    videos = get_videos_by_project(project_id)
    if any(video.get("status") in {"pending", "processing"} for video in videos):
        raise HTTPException(
            status_code=409,
            detail="لا يمكن حذف مشروع أثناء توليد فيديو. انتظر انتهاء العملية ثم أعد المحاولة.",
        )

    deleted = delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")

    jobs_root = JOBS_DIR.resolve()
    storage_root = STORAGE_DIR.resolve()
    for video in videos:
        video_id = str(video.get("video_id", ""))
        job_dir = (JOBS_DIR / video_id).resolve()
        if video_id and job_dir.parent == jobs_root and job_dir.is_dir():
            shutil.rmtree(job_dir, ignore_errors=True)

        file_path = video.get("file_path")
        if file_path:
            candidate = Path(file_path).resolve()
            if candidate.is_file() and any(
                candidate != root and candidate.is_relative_to(root)
                for root in (jobs_root, storage_root)
            ):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass

    project_dir = (STORAGE_DIR / device_id / project_id).resolve()
    if project_dir != storage_root and storage_root in project_dir.parents and project_dir.is_dir():
        shutil.rmtree(project_dir, ignore_errors=True)

    return {
        "success": True,
        "project_id": project_id,
        "deleted_videos": len(videos),
    }


@app.get("/api/videos/{video_id}/stream")
async def api_stream_video(video_id: str, device_id: str = Depends(get_current_device)):
    """Stream a stored video by video_id directly from permanent storage or jobs."""
    _assert_job_owner(video_id, device_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM videos WHERE video_id = ?", (video_id,))
        row = cursor.fetchone()
        if row and row["file_path"] and Path(row["file_path"]).exists():
            return FileResponse(
                path=row["file_path"],
                media_type="video/mp4",
                filename=f"imaginai_{video_id}.mp4",
            )
    
    # Fallback to jobs directory
    job_file = JOBS_DIR / video_id / "output.mp4"
    if job_file.exists():
        return FileResponse(
            path=str(job_file),
            media_type="video/mp4",
            filename=f"imaginai_{video_id}.mp4",
        )

    raise HTTPException(status_code=404, detail="فيديو غير موجود أو لم يكتمل بعد")


@app.get("/api/voices")
async def list_voices(
    project_id: Optional[str] = Query(default=None),
    device_id: str = Depends(get_current_device),
):
    """Return available TTS providers and voices catalog, including user's custom cloned voices."""
    from app.services.fish_audio_tts import fetch_fish_audio_voices

    if project_id:
        _assert_project_owner(project_id, device_id)
    voice_settings = get_project_voice_settings(project_id) if project_id else {}
    
    # Clone catalog list
    providers = json.loads(json.dumps(VOICE_PROVIDERS))
    
    # Dynamically inject Fish Audio voices if available
    fish_voices = fetch_fish_audio_voices(device_id=device_id)

    # Retrieve custom cloned voices for device
    custom_records = get_custom_voices_by_device(device_id)
    custom_voices_list = []
    for r in custom_records:
        custom_voices_list.append({
            "id": r["fish_voice_id"],
            "name": f"{r['name']} (صوتك المستنسخ)",
            "gender": "neutral",
            "lang": "العربية / English",
            "desc": f"صوت مستنسخ محلياً بالذكاء الاصطناعي • {r['name']}",
            "is_custom": True,
        })

    fish_provider = next((p for p in providers if p.get("provider") == "fish-audio"), None)
    if fish_provider is not None:
        available_fish_voices = custom_voices_list + (fish_voices or [])
        if not available_fish_voices:
            available_fish_voices = fish_provider.get("voices", [])
        combined_fish = []
        seen_voice_ids: set[str] = set()
        for voice_item in available_fish_voices:
            voice_id = str(voice_item.get("id", "")).strip()
            if not voice_id or voice_id in seen_voice_ids:
                continue
            setting = voice_settings.get(voice_id, {})
            if setting.get("hidden"):
                continue
            if setting.get("display_name"):
                voice_item = {**voice_item, "name": setting["display_name"]}
            seen_voice_ids.add(voice_id)
            combined_fish.append(voice_item)
        fish_provider["voices"] = combined_fish

    return {"providers": providers}


@app.patch("/api/projects/{project_id}/voices/{voice_id}")
async def api_rename_project_voice(
    project_id: str,
    voice_id: str,
    req: ProjectVoiceRename,
    device_id: str = Depends(get_current_device),
):
    _assert_project_owner(project_id, device_id)
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="يرجى إدخال اسم صالح للصوت.")
    rename_project_voice(project_id, voice_id, name)
    return {"success": True, "project_id": project_id, "voice_id": voice_id, "name": name}


@app.delete("/api/projects/{project_id}/voices/{voice_id}")
async def api_hide_project_voice(
    project_id: str,
    voice_id: str,
    device_id: str = Depends(get_current_device),
):
    _assert_project_owner(project_id, device_id)
    hide_project_voice(project_id, voice_id)
    return {"success": True, "project_id": project_id, "voice_id": voice_id}


@app.post("/api/voices/clone")
async def clone_voice(
    file: UploadFile,
    voice_name: str = Form(...),
    device_id: str = Depends(get_current_device),
):
    """Upload an audio sample, create a cloned voice model in Fish Audio, and save to SQLite."""
    from app.services.fish_audio_tts import clone_voice_fish_audio, generate_speech_fish_audio

    if not file or not voice_name.strip():
        raise HTTPException(status_code=400, detail="يرجى إرفاق ملف صوت واسم للصوت المستنسخ.")

    content = await file.read()
    if not content or len(content) < 1000:
        raise HTTPException(status_code=400, detail="الملف الصوتي المرفق قصير جداً أو فارغ.")

    try:
        fish_voice_id = await clone_voice_fish_audio(
            audio_bytes=content,
            filename=file.filename or "sample.wav",
            voice_name=voice_name.strip(),
            device_id=device_id,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Generate immediate preview audio sample
    previews_dir = JOBS_DIR / "previews"
    previews_dir.mkdir(exist_ok=True)
    preview_path = previews_dir / f"fish-audio_{fish_voice_id}.wav"

    try:
        sample_text = f"مرحباً! هذا التسجيل هو تجربة لاختبار جودة صوتك المستنسخ {voice_name} عبر منصة إيماجن أي آي."
        await generate_speech_fish_audio(sample_text, str(preview_path), voice_id=fish_voice_id, device_id=device_id)
    except Exception as e:
        print(f"[Clone Preview Warning] Could not generate immediate preview for cloned voice: {e}")

    voice_entry_id = str(uuid.uuid4())
    sample_url = f"/api/voices/preview?provider=fish-audio&voice={fish_voice_id}"

    create_custom_voice(
        voice_id=voice_entry_id,
        device_id=device_id,
        name=voice_name.strip(),
        fish_voice_id=fish_voice_id,
        sample_url=sample_url,
    )

    return {
        "success": True,
        "voice": {
            "id": fish_voice_id,
            "name": f"{voice_name.strip()} (صوتك المستنسخ)",
            "provider": "fish-audio",
            "sample_url": sample_url,
            "is_custom": True,
        }
    }


@app.post("/api/fonts/upload")
async def upload_custom_font_endpoint(
    file: UploadFile,
    font_name: str = Form(...),
    device_id: str = Depends(get_current_device),
):
    """Upload a custom font file (.ttf / .otf) and save to SQLite."""
    if not file or not font_name.strip():
        raise HTTPException(status_code=400, detail="يرجى إرفاق ملف الخط واسم الخط.")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in [".ttf", ".otf"]:
        raise HTTPException(status_code=400, detail="صيغة الملف غير مدعومة. يرجى رفع ملف بصيغة ttf أو otf.")

    content = await file.read()
    if not content or len(content) < 100:
        raise HTTPException(status_code=400, detail="ملف الخط فارغ أو غير صالح.")

    font_id = f"font_{uuid.uuid4().hex[:8]}"
    fonts_dir = STORAGE_DIR / device_id / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    font_file_path = fonts_dir / f"{font_id}{ext}"
    font_file_path.write_bytes(content)

    font_entry = create_custom_font(
        font_id=font_id,
        device_id=device_id,
        font_name=font_name.strip(),
        file_path=str(font_file_path),
    )

    return {
        "success": True,
        "font": font_entry,
    }


@app.get("/api/fonts")
async def list_custom_fonts(device_id: str = Depends(get_current_device)):
    """Retrieve all custom fonts for the current device."""
    return {"fonts": get_custom_fonts_by_device(device_id)}



@app.get("/api/voices/preview")
async def preview_voice(
    provider: str = Query("edge-tts", description="TTS Provider (edge-tts, gemini, gtts, fish-audio)"),
    voice: str = Query("ar-SA-HamedNeural", description="Voice ID"),
    refresh: bool = Query(False, description="Force regenerate preview audio"),
    device_id: str = Depends(get_current_device),
):
    """Generate and stream a short preview audio sample for the selected voice."""
    previews_dir = JOBS_DIR / "previews"
    previews_dir.mkdir(exist_ok=True)

    # Sanitize file key
    safe_voice = voice.replace("/", "_").replace("\\", "_")
    safe_provider = provider.replace("/", "_").replace("\\", "_")
    preview_file = previews_dir / f"{safe_provider}_{safe_voice}.wav"

    if refresh or not preview_file.exists() or preview_file.stat().st_size < 1000:
        if preview_file.exists():
            try:
                preview_file.unlink()
            except Exception:
                pass

        is_english = voice.startswith("en") or voice in ["Christopher", "Jenny", "Guy", "Fenrir"]
        is_female = any(name in voice for name in ["Salma", "Zariyah", "Kore", "Aoede", "Jenny"])

        if is_english:
            sample_text = "Hello! This is a sample preview of the selected narration voice for Imagin AI."
        elif is_female:
            sample_text = "أهلاً بك! أنا سلمى، وهذا تسجيل صوتي أنثوي لتجربة نبرة السرد في منصة إيماجن أي آي."
        else:
            sample_text = "مرحباً بك! أنا حامد، وهذا تسجيل صوتي ذكوري رزين لتجربة نبرة السرد في منصة إيماجن أي آي."

        from app.services.gemini_tts import generate_speech
        try:
            await generate_speech(
                text=sample_text,
                output_path=str(preview_file),
                voice=voice,
                provider=provider,
                device_id=device_id,
            )
        except Exception as e:
            err_msg = str(e)
            if "FISH_AUDIO_API_KEY" in err_msg:
                raise HTTPException(status_code=400, detail="يرجى إدخال مفتاح FISH_AUDIO_API_KEY في ملف .env لاستماع وتوليد أصوات Fish Audio.")
            raise HTTPException(status_code=400, detail=f"Failed to generate voice preview: {err_msg}")

    return FileResponse(
        path=str(preview_file),
        media_type="audio/wav",
        filename=f"preview_{safe_voice}.wav",
    )


@app.post("/api/jobs")
async def create_job(
    req: JobRequest,
    bg: BackgroundTasks,
    request: Request,
    response: Response,
    device_id: str = Depends(get_current_device),
    x_visitor_id: str = Header(None, alias="x-visitor-id"),
    x_idempotency_key: str | None = Header(None, alias="x-idempotency-key"),
):
    # Determine visitor identity
    visitor_id = device_id or x_visitor_id or f"ip_{request.client.host if request.client else '127.0.0.1'}"

    # Rate limiting check
    allowed, err_msg = check_and_increment_rate_limits(visitor_id)
    if not allowed:
        raise HTTPException(status_code=429, detail=err_msg)

    # Project handling
    project_id = req.project_id
    domain = req.domain or "عام"
    niche = req.niche or "محتوى عام"

    from app.niches import find_niche_info
    if req.niche_id and not find_niche_info(req.niche_id) and not req.custom_niche:
        raise HTTPException(status_code=400, detail="معرّف النيتش غير صالح.")
    allowed_source_ids = {
        "pexels_video", "pixabay_video", "pexels_image", "pixabay_image",
        "coverr_video", "nara", "nasa", "wikimedia", "archive_org", "loc",
    }
    if req.niche_sources is not None:
        unknown_sources = set(req.niche_sources) - allowed_source_ids
        if unknown_sources:
            raise HTTPException(status_code=400, detail="تتضمن قائمة المصادر اختياراً غير معروف.")
        if not req.niche_sources:
            raise HTTPException(status_code=400, detail="اختر مصدراً واحداً على الأقل للوسائط.")

    if x_idempotency_key:
        for existing_dir in JOBS_DIR.iterdir() if JOBS_DIR.exists() else []:
            request_file = existing_dir / "request.json"
            if not request_file.exists():
                continue
            try:
                saved = json.loads(request_file.read_text(encoding="utf-8"))
                if saved.get("device_id") == device_id and saved.get("idempotency_key") == x_idempotency_key:
                    return {
                        "job_id": existing_dir.name,
                        "project_id": saved.get("project_id"),
                        "device_id": device_id,
                        "duplicate": True,
                    }
            except Exception:
                continue

    if project_id:
        _assert_project_owner(project_id, device_id)
        project = get_project(project_id)
        if project:
            domain = project.get("domain") or domain
            niche = project.get("niche") or niche

    if not project_id:
        existing_projects = get_projects_by_device(device_id)
        if existing_projects:
            project_id = existing_projects[0]["project_id"]
        else:
            default_proj = create_project(
                project_id=f"proj_{uuid.uuid4().hex[:8]}",
                device_id=device_id,
                domain=domain,
                niche=niche,
                name="المشروع الافتراضي",
            )
            project_id = default_proj["project_id"]

    job_id = uuid.uuid4().hex[:12]
    job_path = _job_dir(job_id)
    job_path.mkdir(parents=True, exist_ok=True)

    # Derive title for video
    title = (req.idea or req.provided_script or "فيديو جديد")[:50]
    create_video(
        video_id=job_id,
        project_id=project_id,
        title=title,
        status="processing",
    )

    # Initialize status.json
    status = make_initial_status(job_id)
    _write_status(job_id, status)

    # Save the request
    (job_path / "request.json").write_text(
        json.dumps(
            {
                "idea": req.idea,
                "duration": req.duration,
                "style": req.style,
                "voice_provider": req.voice_provider,
                "voice": req.voice,
                "visitor_id": visitor_id,
                "device_id": device_id,
                "project_id": project_id,
                "input_mode": req.input_mode,
                "provided_script": req.provided_script,
                "niche_id": req.niche_id,
                "custom_niche": req.custom_niche,
                "custom_niche_description": req.custom_niche_description,
                "niche_sources": req.niche_sources,
                "additional_context": req.additional_context,
                "strict_focus": req.strict_focus,
                "caption_config": req.caption_config,
                "idempotency_key": x_idempotency_key,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Run pipeline in background
    from app.pipeline import run_pipeline
    bg.add_task(
        run_pipeline,
        job_id=job_id,
        idea=req.idea or "",
        duration=req.duration,
        style=req.style,
        voice=req.voice,
        voice_provider=req.voice_provider,
        device_id=device_id,
        project_id=project_id,
        niche_id=req.niche_id,
        custom_niche=req.custom_niche,
        custom_niche_description=req.custom_niche_description,
        niche_sources=req.niche_sources,
        caption_config=req.caption_config,
        input_mode=req.input_mode,
        provided_script=req.provided_script,
        additional_context=req.additional_context,
    )

    return {"job_id": job_id, "project_id": project_id, "device_id": device_id}


@app.get("/api/settings/api-keys")
async def get_api_key_settings(device_id: str = Depends(get_current_device)):
    return {"providers": list_key_metadata(device_id)}


@app.post("/api/settings/api-keys")
async def save_api_key_settings(req: ApiKeySaveRequest, device_id: str = Depends(get_current_device)):
    try:
        return {"success": True, **save_user_key(device_id, req.provider, req.api_key)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.delete("/api/settings/api-keys/{provider}/{key_id}")
async def delete_api_key_settings(provider: str, key_id: str, device_id: str = Depends(get_current_device)):
    if not delete_user_key(device_id, provider, key_id):
        raise HTTPException(status_code=404, detail="API key not found.")
    return {"success": True}


@app.post("/api/settings/api-keys/test")
async def test_api_key_settings(req: ApiKeyTestRequest, device_id: str = Depends(get_current_device)):
    from app.services.provider_tests import test_provider
    try:
        api_key = get_user_key_by_id(device_id, req.provider, req.key_id)
    except (KeyError, RuntimeError):
        raise HTTPException(status_code=404, detail="API key not found.")

    result = await test_provider(req.provider, api_key)
    record_test_result(
        device_id=device_id,
        provider=req.provider,
        key_id=req.key_id,
        status=result["status"],
        error=result.get("message"),
    )
    return {"success": result["status"] == "valid", **result}


VIDEO_SOURCE_CATALOG = [
    {"id": "pexels_video", "label": "Pexels", "key_provider": "pexels", "requires_key": True},
    {"id": "pixabay_video", "label": "Pixabay", "key_provider": "pixabay", "requires_key": True},
    {"id": "coverr_video", "label": "Coverr", "key_provider": "coverr", "requires_key": True},
    {"id": "nara", "label": "NARA", "key_provider": "nara", "requires_key": True},
    {"id": "nasa", "label": "NASA Images and Video Library", "requires_key": False},
    {"id": "wikimedia", "label": "Wikimedia Commons", "requires_key": False},
    {"id": "archive_org", "label": "Internet Archive", "requires_key": False},
    {"id": "loc", "label": "Library of Congress", "requires_key": False},
]


def _source_key(provider: str, device_id: str) -> str:
    getters = {
        "pexels": get_pexels_key,
        "pixabay": get_pixabay_key,
        "coverr": get_coverr_key,
        "nara": get_nara_key,
    }
    return getters[provider](device_id=device_id)


@app.get("/api/video-sources")
async def get_video_source_catalog(device_id: str = Depends(get_current_device)):
    """Describe video sources without exposing any configured credential values."""
    sources = []
    for source in VIDEO_SOURCE_CATALOG:
        item = {**source}
        item["enabled"] = not (source["id"] == "coverr_video" and not COVERR_ENABLED)
        if source["id"] == "coverr_video" and not item["enabled"]:
            item["connectionStatus"] = "disabled"
        elif source["requires_key"]:
            try:
                _source_key(source["key_provider"], device_id)
                item["connectionStatus"] = "configured_not_tested"
            except RuntimeError:
                item["connectionStatus"] = "missing_key"
        else:
            item["connectionStatus"] = "not_tested"
        sources.append(item)
    return {"sources": sources}


@app.post("/api/video-sources/{source_id}/test")
async def test_video_source(source_id: str, device_id: str = Depends(get_current_device)):
    """Make a low-cost live request and report success only for a real valid response."""
    source = next((item for item in VIDEO_SOURCE_CATALOG if item["id"] == source_id), None)
    if not source:
        raise HTTPException(status_code=404, detail="مصدر الفيديو غير معروف.")
    if source_id == "coverr_video" and not COVERR_ENABLED:
        return {"status": "disabled", "message": "Coverr معطّل افتراضياً. فعّل COVERR_ENABLED أولاً."}

    if source["requires_key"]:
        from app.services.provider_tests import test_provider
        try:
            key = _source_key(source["key_provider"], device_id)
        except RuntimeError:
            return {"status": "not_configured", "message": "أضف مفتاح الخدمة واختبره أولاً."}
        result = await test_provider(source["key_provider"], key)
        return {"success": result["status"] == "valid", **result}

    checks = {
        "nasa": (
            "https://images-api.nasa.gov/search",
            {"q": "moon", "media_type": "video", "page_size": "1"},
            lambda payload: "collection" in payload,
        ),
        "wikimedia": (
            "https://commons.wikimedia.org/w/api.php",
            {"action": "query", "format": "json", "meta": "siteinfo"},
            lambda payload: "query" in payload,
        ),
        "archive_org": (
            "https://archive.org/advancedsearch.php",
            {"q": "mediatype:movies", "rows": "1", "output": "json"},
            lambda payload: "response" in payload,
        ),
        "loc": (
            "https://www.loc.gov/pictures/search/",
            {"q": "space", "fo": "json", "c": "1"},
            lambda payload: "results" in payload,
        ),
    }
    url, params, accepts_response = checks[source_id]
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            if not accepts_response(response.json()):
                return {"status": "invalid", "message": "وصل رد غير متوقع من الخدمة."}
        return {"status": "valid", "success": True, "message": "نجح الاتصال بالمصدر."}
    except Exception as exc:
        return {
            "status": "unavailable",
            "success": False,
            "message": f"تعذر الاتصال بالمصدر ({type(exc).__name__}).",
        }


@app.post("/api/video-sources/search")
async def search_video_source_assets(
    req: VideoSourceSearchRequest,
    device_id: str = Depends(get_current_device),
):
    source_ids = req.source_ids or [
        "archive_org", "loc", "wikimedia", "nasa", "nara",
        "pixabay_video", "pexels_video", "pixabay_image", "pexels_image",
    ]
    allowed_source_ids = {
        "pexels_video", "pixabay_video", "pexels_image", "pixabay_image",
        "coverr_video", "nara", "nasa", "wikimedia", "archive_org", "loc",
    }
    if set(source_ids) - allowed_source_ids:
        raise HTTPException(status_code=400, detail="تتضمن قائمة المصادر اختياراً غير معروف.")
    from app.services.footage import search_video_sources
    assets = await search_video_sources(
        keywords=[item.strip() for item in req.keywords if item.strip()],
        source_ids=source_ids,
        device_id=device_id,
        limit=req.limit,
    )
    return {"assets": assets}


@app.post("/api/feedback")
async def receive_feedback(payload: dict):
    """Receive user feedback / suggestions and store in backend/data/feedback.json."""
    message = payload.get("message", "").strip()
    contact = payload.get("contact", "").strip()
    
    if not message:
        raise HTTPException(status_code=400, detail="الرجاء كتابة رسالة قبل الإرسال.")

    feedback_file = DATA_DIR / "feedback.json"
    existing = []
    if feedback_file.exists():
        try:
            existing = json.loads(feedback_file.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    existing.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "contact": contact,
    })
    
    feedback_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "message": "شكراً لك! تم استلام ملاحظتك بنجاح."}


@app.get("/api/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(job_id: str, device_id: str = Depends(get_current_device)):
    _assert_job_owner(job_id, device_id)
    return _read_status(job_id)


@app.get("/api/jobs/{job_id}/download")
async def download_video(job_id: str, device_id: str = Depends(get_current_device)):
    _assert_job_owner(job_id, device_id)
    # First check permanent storage, fallback to jobs output.mp4
    from app.db import get_db_connection
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM videos WHERE video_id = ?", (job_id,))
        row = cursor.fetchone()
        if row and row["file_path"] and Path(row["file_path"]).exists():
            return FileResponse(
                path=row["file_path"],
                media_type="video/mp4",
                filename=f"imaginai_{job_id}.mp4",
            )

    output = _job_dir(job_id) / "output.mp4"
    if not output.exists():
        raise HTTPException(status_code=404, detail="Video not ready yet")
    return FileResponse(
        path=str(output),
        media_type="video/mp4",
        filename=f"imaginai_{job_id}.mp4",
    )


@app.get("/api/jobs/{job_id}/review")
async def get_job_review_data(job_id: str, device_id: str = Depends(get_current_device)):
    """Retrieve review_data.json for a job in pending_review status."""
    _assert_job_owner(job_id, device_id)
    review_file = _job_dir(job_id) / "review_data.json"
    if not review_file.exists():
        raise HTTPException(status_code=404, detail="بيانات المراجعة غير موجودة أو لم تكتمل مرحلة اللقطات بعد.")
    try:
        data = json.loads(review_file.read_text(encoding="utf-8"))
        # Internal filesystem paths are never exposed to the browser. The
        # browser already receives an authenticated stream_url for each shot.
        for scene in data.get("scenes", []):
            scene.pop("clip_path", None)
            scene.pop("audio_path", None)
            for shot in scene.get("shots", []):
                shot.pop("clip_path", None)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"خطأ في قراءة بيانات المراجعة: {str(e)}")


@app.get("/api/jobs/{job_id}/clips/{scene_index}/{shot_index}")
async def get_job_clip(job_id: str, scene_index: int, shot_index: int, device_id: str = Depends(get_current_device)):
    """Stream a video clip associated with a job scene for review preview."""
    _assert_job_owner(job_id, device_id)
    review_file = _job_dir(job_id) / "review_data.json"
    if not review_file.exists():
        raise HTTPException(status_code=404, detail="الملف غير موجود.")
    data = json.loads(review_file.read_text(encoding="utf-8"))
    scenes = data.get("scenes", [])
    if scene_index < 0 or scene_index >= len(scenes):
        raise HTTPException(status_code=404, detail="المشهد غير موجود.")
    shots = scenes[scene_index].get("shots", [])
    if shot_index < 0 or shot_index >= len(shots):
        raise HTTPException(status_code=404, detail="اللقطة غير موجودة.")
    clip_path = Path(shots[shot_index].get("clip_path", ""))
    try:
        clip_path.resolve().relative_to(_job_dir(job_id).resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="مسار اللقطة غير صالح.")
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="ملف اللقطة غير موجود على السيرفر.")
    return FileResponse(clip_path, media_type="video/mp4")


@app.post("/api/jobs/{job_id}/resume")
async def resume_job_endpoint(
    job_id: str,
    req: ResumeJobRequest,
    background_tasks: BackgroundTasks,
    device_id: str = Depends(get_current_device),
):
    """Resume a job paused at pending_review after applying user edits."""
    _assert_job_owner(job_id, device_id)
    status_file = _job_dir(job_id) / "status.json"
    if not status_file.exists():
        raise HTTPException(status_code=404, detail="الـ Job غير موجود.")
    
    st = json.loads(status_file.read_text(encoding="utf-8"))
    review_file = _job_dir(job_id) / "review_data.json"
    recoverable_render_failure = (
        st.get("status") == "failed"
        and st.get("stages", {}).get("footage") == "done"
        and st.get("stages", {}).get("render") in {"processing", "failed"}
        and review_file.is_file()
    )
    if st.get("status") != "pending_review" and not recoverable_render_failure:
        raise HTTPException(status_code=400, detail="الـ Job ليس في حالة انتظار المراجعة (pending_review).")

    try:
        review_data = json.loads(review_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="تعذر التحقق من بيانات ترخيص اللقطات.")
    needs_license_review = any(
        (shot.get("metadata") or {}).get("rightsStatus") != "valid"
        for scene in review_data.get("scenes", [])
        for shot in scene.get("shots", [])
    )
    if needs_license_review and not req.license_review_confirmed:
        raise HTTPException(
            status_code=400,
            detail="توجد لقطات تحتاج إلى مراجعة الترخيص. راجع مصدر كل لقطة وأكد المتابعة قبل التصدير.",
        )

    st["status"] = "processing"
    st["current_stage"] = "captions"
    st["stages"]["captions"] = "processing"
    st["stages"]["render"] = "pending"
    st["error"] = None
    status_file.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")

    from app.db import update_video
    update_video(video_id=job_id, status="processing")

    from app.pipeline import resume_pipeline
    edited_dicts = [item.model_dump() for item in req.edited_scenes]
    background_tasks.add_task(resume_pipeline, job_id, edited_dicts)

    return {"success": True, "message": "تم استئناف الرندر النهائي بنجاح!"}


@app.post("/api/jobs/{job_id}/approve-sections")
async def approve_sections_endpoint(
    job_id: str,
    req: SectionApprovalRequest,
    background_tasks: BackgroundTasks,
    device_id: str = Depends(get_current_device),
):
    """Approve or decline proposed section overlays for dynamic section analysis."""
    _assert_job_owner(job_id, device_id)
    status_file = _job_dir(job_id) / "status.json"
    if not status_file.exists():
        raise HTTPException(status_code=404, detail="الـ Job غير موجود.")

    st = json.loads(status_file.read_text(encoding="utf-8"))
    if st.get("status") != "pending_section_approval":
        raise HTTPException(status_code=400, detail="الـ Job ليس في حالة انتظار موافقة الأقسام.")

    st["status"] = "processing"
    st["current_stage"] = "tts"
    st["stages"]["tts"] = "processing"
    status_file.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")

    from app.db import update_video
    update_video(video_id=job_id, status="processing")

    from app.pipeline import approve_sections_and_resume
    background_tasks.add_task(approve_sections_and_resume, job_id, req.apply_sections)

    return {"success": True, "message": "تمت معالجة قرار الأقسام واستئناف التوليد بنجاح!"}


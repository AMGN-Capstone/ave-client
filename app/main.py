from __future__ import annotations

import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_media_root, get_supabase_anon_key, get_supabase_url
from app.schemas import (
    AuthUserResponse,
    PublicConfigResponse,
    UploadVideoResponse,
    YouTubeImportRequest,
    YouTubeImportResponse,
    LiveChatResponse,
    LiveYouTubeRequest,
    LiveFinalizeRequest,
    LiveFinalizeResponse,
    LiveEditRequest,
    SegmentSelectionRequest,
    SubtitleUpdateRequest,
)
from app.services.supabase_service import (
    get_auth_client,
    insert_row,
    is_configured,
    is_server_configured,
    update_row,
    upload_file,
)
from app.services.youtube_importer import (
    InvalidYouTubeURLError,
    YouTubeImporter,
    YouTubeImportError,
)
from app.services.live_youtube_service import (
    LiveYouTubeError,
    _chat_archive_path,
    _read_chat_session,
    _write_chat_session,
    analyze_chat_archive,
    collect_chat_replay,
    extract_video_id,
    get_live_broadcast,
    get_live_chat,
    get_video_metadata,
)
from app.services.live_edit_pipeline import LiveEditPipeline, LiveEditPipelineError


SUPPORTED_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
CHUNK_SIZE = 1024 * 1024
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


app = FastAPI(title="Automatic Video Editor MVP")
auth_scheme = HTTPBearer(auto_error=False)
LIVE_EDIT_JOBS: dict[str, dict] = {}
EDIT_JOB_LOCKS: dict[str, asyncio.Lock] = {}

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_youtube_importer() -> YouTubeImporter:
    return YouTubeImporter(get_media_root())


def _user_value(user, key: str):
    if isinstance(user, dict):
        return user.get(key)
    return getattr(user, key, None)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Google login is required.")
    if not is_configured():
        raise HTTPException(status_code=503, detail="Supabase Auth is not configured.")

    try:
        response = get_auth_client().auth.get_user(credentials.credentials)
        user = _user_value(response, "user")
        user_id = _user_value(user, "id")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired login session.") from exc

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid login session.")
    return user


def _local_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    return path if path.is_absolute() else Path.cwd() / path


def _subtitle_text(path: Path) -> tuple[str, list[dict]]:
    if not path.exists():
        return "", []
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [line.strip() for line in raw.splitlines()]
    text_lines = []
    for line in lines:
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit():
            continue
        if line.startswith("NOTE"):
            continue
        text_lines.append(line)
    return "\n".join(text_lines), [{"text": line} for line in text_lines]


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend has not been built.")
    return FileResponse(index_path)


@app.get("/api/config", response_model=PublicConfigResponse)
async def public_config():
    if not is_configured():
        raise HTTPException(status_code=503, detail="Supabase is not configured.")
    return {
        "supabase_url": get_supabase_url(),
        "supabase_anon_key": get_supabase_anon_key(),
    }


@app.get("/api/auth/me", response_model=AuthUserResponse)
async def auth_me(user=Depends(get_current_user)):
    return {"id": str(_user_value(user, "id")), "email": _user_value(user, "email")}


@app.post("/api/youtube/import", response_model=YouTubeImportResponse)
async def import_youtube_video(
    request: YouTubeImportRequest,
    importer: YouTubeImporter = Depends(get_youtube_importer),
    user=Depends(get_current_user),
):
    if not is_server_configured():
        raise HTTPException(
            status_code=503,
            detail="SUPABASE_SERVICE_ROLE_KEY is not configured on the server.",
        )

    user_id = str(_user_value(user, "id"))
    job_id = str(uuid4())
    video_id = str(uuid4())
    insert_row(
        "videos",
        {"id": video_id, "user_id": user_id, "source_url": request.url},
    )
    insert_row(
        "processing_jobs",
        {
            "id": job_id,
            "user_id": user_id,
            "video_id": video_id,
            "kind": "import",
            "status": "downloading",
            "progress": 5,
        },
    )

    try:
        result = await importer.import_video(request.url, job_id=job_id)
        storage_paths = {}

        video_path = _local_path(result.get("video_path"))
        if video_path and video_path.exists():
            storage_paths["video"] = f"{user_id}/{job_id}/video{video_path.suffix.lower()}"
            upload_file(storage_paths["video"], video_path, "video/mp4")

        subtitle_text = ""
        transcript_segments = []
        for index, subtitle_value in enumerate(result.get("subtitle_files", [])):
            subtitle_path = _local_path(subtitle_value)
            if not subtitle_path or not subtitle_path.exists():
                continue
            storage_path = f"{user_id}/{job_id}/subtitle-{index}{subtitle_path.suffix.lower()}"
            storage_paths[f"subtitle_{index}"] = storage_path
            upload_file(storage_path, subtitle_path, "text/vtt")
            if not subtitle_text:
                subtitle_text, transcript_segments = _subtitle_text(subtitle_path)

        metadata_path = _local_path(result.get("metadata_path"))
        if metadata_path and metadata_path.exists():
            storage_paths["metadata"] = f"{user_id}/{job_id}/metadata.json"
            upload_file(storage_paths["metadata"], metadata_path, "application/json")

        metadata = {}
        if metadata_path and metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        update_row(
            "videos",
            video_id,
            {
                "title": result.get("title"),
                "channel_name": metadata.get("channel"),
                "duration_sec": result.get("duration"),
                "storage_path": storage_paths.get("video"),
                "metadata": {**metadata, "storage_paths": storage_paths},
            },
        )

        transcript_id = None
        if subtitle_text:
            transcript_id = str(uuid4())
            insert_row(
                "transcripts",
                {
                    "id": transcript_id,
                    "user_id": user_id,
                    "video_id": video_id,
                    "language": "ko",
                    "source": "youtube_caption",
                    "content": subtitle_text,
                    "segments": transcript_segments,
                    "storage_path": next(
                        (path for key, path in storage_paths.items() if key.startswith("subtitle_")),
                        None,
                    ),
                },
            )

        update_row(
            "processing_jobs",
            job_id,
            {
                "status": "completed",
                "progress": 100,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {
            **result,
            "video_id": video_id,
            "transcript_id": transcript_id,
            "video_path": storage_paths.get("video"),
            "subtitle_files": [
                path for key, path in storage_paths.items() if key.startswith("subtitle_")
            ],
            "metadata_path": storage_paths.get("metadata", ""),
        }
    except InvalidYouTubeURLError as exc:
        update_row("processing_jobs", job_id, {"status": "failed", "error_message": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except YouTubeImportError as exc:
        update_row("processing_jobs", job_id, {"status": "failed", "error_message": str(exc)})
        raise HTTPException(status_code=502, detail=f"YouTube import failed: {exc}") from exc
    except Exception as exc:
        update_row("processing_jobs", job_id, {"status": "failed", "error_message": str(exc)})
        raise HTTPException(status_code=502, detail=f"Supabase save failed: {exc}") from exc


@app.post("/api/videos/upload", response_model=UploadVideoResponse)
async def upload_video(file: UploadFile = File(...)):
    original_filename = Path(file.filename or "").name
    extension = Path(original_filename).suffix.lower()

    if extension not in SUPPORTED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported video format.")

    upload_dir = get_media_root() / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored_filename = f"{uuid4().hex}{extension}"
    stored_path = upload_dir / stored_filename
    size_bytes = 0

    with stored_path.open("wb") as output_file:
        while chunk := await file.read(CHUNK_SIZE):
            size_bytes += len(chunk)
            output_file.write(chunk)

    return {
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "content_type": file.content_type,
        "size_bytes": size_bytes,
        "path": stored_path.resolve().as_posix(),
    }


@app.post("/api/youtube/live/inspect")
async def inspect_live_youtube(
    request: LiveYouTubeRequest,
    youtube_access_token: str = Header(..., alias="X-YouTube-Access-Token"),
):
    try:
        video_id = extract_video_id(request.url)
        broadcast = get_live_broadcast(youtube_access_token, video_id)
        # Chat is fetched by the separate polling endpoint below. Calling
        # liveChatMessages here and immediately polling again can trigger
        # YouTube's rateLimitExceeded response.
        session = {
            "broadcast_id": video_id,
            "video_id": video_id,
            "live_chat_id": broadcast.get("live_chat_id"),
            "actual_start_time": broadcast.get("actual_start_time"),
            "title": broadcast.get("title"),
            "status": broadcast.get("life_cycle_status"),
        }
        if not broadcast.get("live_chat_id"):
            session["warning"] = (
                "활성 라이브 채팅이 없습니다. 방송 중에 먼저 추적을 시작해야 "
                "다시보기와 결합할 채팅 데이터가 저장됩니다."
            )
        if broadcast.get("live_chat_id"):
            _write_chat_session(broadcast["live_chat_id"], session)

        return {"broadcast": broadcast, "session": session}
    except LiveYouTubeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/youtube/live/chat", response_model=LiveChatResponse)
async def live_youtube_chat(
    live_chat_id: str = Query(..., min_length=1),
    page_token: str | None = Query(default=None),
    actual_start_time: str | None = Query(default=None),
    delay_seconds: float = Query(default=0.0, ge=0, le=120),
    youtube_access_token: str = Header(..., alias="X-YouTube-Access-Token"),
):
    try:
        return get_live_chat(
            youtube_access_token,
            live_chat_id,
            page_token,
            actual_start_time=actual_start_time,
            delay_seconds=delay_seconds,
        )
    except LiveYouTubeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _update_live_edit_job(job_id: str, **values) -> None:
    job = LIVE_EDIT_JOBS.get(job_id)
    if job is not None:
        job.update(values)


async def _run_live_edit_job(
    job_id: str,
    request: LiveEditRequest,
) -> None:
    try:
        _update_live_edit_job(job_id, status="running", progress=3, message="업로드된 영상을 확인하는 중입니다.")
        vod_id = extract_video_id(request.vod_url)
        archive_key = f"vod-{vod_id}"
        archive_path = _chat_archive_path(archive_key)

        if not archive_path.exists() or archive_path.stat().st_size == 0:
            _update_live_edit_job(job_id, progress=8, message="지원되는 채팅 리플레이를 확인하는 중입니다.")
            try:
                await asyncio.to_thread(collect_chat_replay, vod_id, archive_key)
            except LiveYouTubeError:
                # Chat replay is an optional signal. A completed video without
                # replay chat must still be editable from its transcript.
                pass

        pipeline = LiveEditPipeline(get_media_root())
        result = await asyncio.to_thread(
            pipeline.run,
            job_id=job_id,
            vod_url=request.vod_url,
            archive_path=archive_path,
            genre=request.genre,
            actual_start_time=None,
            target_seconds=request.target_duration_seconds,
            chat_delay_seconds=request.chat_delay_seconds,
            clean_subtitles=request.clean_subtitles,
            delay_seconds=0.0,
            subtitle_offset_seconds=request.subtitle_offset_seconds,
            subtitle_font_name=request.subtitle_font_name,
            subtitle_font_size=request.subtitle_font_size,
            render_mode=request.render_mode,
            defer_render=request.interactive_selection,
            progress_callback=lambda progress, message: _update_live_edit_job(
                job_id, progress=progress, message=message
            ),
        )
        result["vod_video_id"] = vod_id
        result["chat_replay_used"] = archive_path.exists() and archive_path.stat().st_size > 0
        if result.get("awaiting_selection"):
            _update_live_edit_job(
                job_id,
                status="awaiting_selection",
                progress=100,
                phase="selection",
                message="AI 분석이 완료되었습니다. 원하는 구간을 선택하세요.",
                result=result,
            )
        else:
            _update_live_edit_job(
                job_id,
                status="completed",
                progress=100,
                phase="render",
                message="AI 영상 편집이 완료되었습니다.",
                result=result,
            )
    except (LiveYouTubeError, LiveEditPipelineError) as exc:
        _update_live_edit_job(job_id, status="failed", progress=100, message=str(exc), error=str(exc))
    except Exception as exc:
        _update_live_edit_job(
            job_id,
            status="failed",
            progress=100,
            message=f"AI 영상 편집에 실패했습니다: {exc}",
            error=f"AI 영상 편집에 실패했습니다: {exc}",
        )


@app.post("/api/youtube/edit/start", status_code=202)
@app.post("/api/youtube/live/edit/start", status_code=202, deprecated=True)
async def start_live_edit(
    request: LiveEditRequest,
):
    job_id = uuid4().hex
    LIVE_EDIT_JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "phase": "analysis",
        "message": "AI 편집 작업을 준비하는 중입니다.",
    }
    asyncio.create_task(_run_live_edit_job(job_id, request))
    return LIVE_EDIT_JOBS[job_id]


@app.get("/api/youtube/edit/status/{job_id}")
@app.get("/api/youtube/live/edit/status/{job_id}", deprecated=True)
async def live_edit_status(job_id: str):
    job = LIVE_EDIT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="AI 편집 작업을 찾을 수 없습니다.")
    return job


async def _run_segment_selection_job(
    job_id: str,
    request: SegmentSelectionRequest,
) -> None:
    lock = EDIT_JOB_LOCKS.setdefault(job_id, asyncio.Lock())
    async with lock:
        previous_result = dict(LIVE_EDIT_JOBS.get(job_id, {}).get("result") or {})
        try:
            _update_live_edit_job(
                job_id,
                status="running",
                progress=0,
                phase="render",
                message="사용자가 선택한 구간으로 편집을 준비하는 중입니다.",
            )
            pipeline = LiveEditPipeline(get_media_root())
            result = await asyncio.to_thread(
                pipeline.rerender_from_selection,
                job_id,
                request.segment_ids,
                feedback=request.feedback,
                progress_callback=lambda progress, message: _update_live_edit_job(
                    job_id,
                    progress=progress,
                    message=message,
                ),
            )
            merged_result = {
                **previous_result,
                **result,
                "awaiting_selection": False,
            }
            _update_live_edit_job(
                job_id,
                status="completed",
                progress=100,
                phase="render",
                message="선택한 구간으로 영상을 다시 만들었습니다.",
                result=merged_result,
                error=None,
            )
        except LiveEditPipelineError as exc:
            _update_live_edit_job(
                job_id,
                status="failed",
                progress=100,
                phase="render",
                message=str(exc),
                error=str(exc),
                result=previous_result,
            )
        except Exception as exc:
            message = f"선택 구간 렌더링에 실패했습니다: {exc}"
            _update_live_edit_job(
                job_id,
                status="failed",
                progress=100,
                phase="render",
                message=message,
                error=message,
                result=previous_result,
            )


def _edit_output_dir(job_id: str) -> Path:
    if not job_id or Path(job_id).name != job_id:
        raise HTTPException(status_code=400, detail="잘못된 편집 작업 ID입니다.")
    directory = get_media_root() / "youtube-live-edit" / job_id
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="편집 작업을 찾을 수 없습니다.")
    return directory


@app.get("/api/youtube/edit/{job_id}/segments")
@app.get("/api/youtube/live/edit/{job_id}/segments", deprecated=True)
async def get_edit_segments(job_id: str):
    _edit_output_dir(job_id)
    pipeline = LiveEditPipeline(get_media_root())
    try:
        return pipeline.get_segment_review(job_id)
    except LiveEditPipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/youtube/edit/{job_id}/segments", status_code=202)
@app.put("/api/youtube/live/edit/{job_id}/segments", status_code=202, deprecated=True)
async def update_edit_segments(job_id: str, request: SegmentSelectionRequest):
    _edit_output_dir(job_id)
    current = LIVE_EDIT_JOBS.get(job_id)
    if current and current.get("phase") == "render" and current.get("status") in {
        "queued",
        "running",
    }:
        raise HTTPException(status_code=409, detail="이미 선택 구간을 렌더링하고 있습니다.")
    if current is None:
        LIVE_EDIT_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0,
            "phase": "render",
            "message": "선택 구간 렌더링을 준비하는 중입니다.",
        }
    else:
        current.update(
            {
                "status": "queued",
                "progress": 0,
                "phase": "render",
                "message": "선택 구간 렌더링을 준비하는 중입니다.",
                "error": None,
            }
        )
    asyncio.create_task(_run_segment_selection_job(job_id, request))
    return LIVE_EDIT_JOBS[job_id]


@app.get("/api/youtube/edit/{job_id}/media/{kind}")
@app.get("/api/youtube/live/edit/{job_id}/media/{kind}", deprecated=True)
async def get_edit_media(job_id: str, kind: str):
    output_dir = _edit_output_dir(job_id)
    plan_path = output_dir / "edit_plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail="편집 계획 파일을 찾을 수 없습니다.") from exc

    if kind == "source":
        media_path = Path(str(plan.get("source_video_path", "")))
        if not media_path.is_absolute():
            media_path = (Path.cwd() / media_path).resolve()
    elif kind == "rendered":
        render_mode = str(plan.get("render_mode", "preview"))
        filename = str(plan.get("rendered_filename") or f"edited-{render_mode}.mp4")
        if Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="저장된 영상 경로가 올바르지 않습니다.")
        media_path = output_dir / filename
    else:
        raise HTTPException(status_code=404, detail="지원하지 않는 영상 종류입니다.")
    if not media_path.exists() or not media_path.is_file():
        raise HTTPException(status_code=404, detail="영상 파일을 찾을 수 없습니다.")
    return FileResponse(
        media_path,
        media_type="video/mp4",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/youtube/edit/{job_id}/subtitles")
@app.get("/api/youtube/live/edit/{job_id}/subtitles", deprecated=True)
async def get_edit_subtitles(job_id: str):
    subtitles = _edit_output_dir(job_id) / "subtitles.srt"
    if not subtitles.exists():
        raise HTTPException(status_code=404, detail="자막 파일을 찾을 수 없습니다.")
    return {
        "job_id": job_id,
        "content": subtitles.read_text(encoding="utf-8-sig", errors="replace"),
        "path": str(subtitles.resolve()),
    }


@app.put("/api/youtube/edit/{job_id}/subtitles")
@app.put("/api/youtube/live/edit/{job_id}/subtitles", deprecated=True)
async def update_edit_subtitles(job_id: str, request: SubtitleUpdateRequest):
    output_dir = _edit_output_dir(job_id)
    subtitles = output_dir / "subtitles.srt"
    subtitles.write_text(request.content, encoding="utf-8-sig")
    pipeline = LiveEditPipeline(get_media_root())
    try:
        result = await asyncio.to_thread(pipeline.rerender_from_saved_subtitles, job_id)
    except LiveEditPipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if job_id in LIVE_EDIT_JOBS and LIVE_EDIT_JOBS[job_id].get("result"):
        LIVE_EDIT_JOBS[job_id]["result"].update(result)
    return {**result, "message": "자막을 저장하고 영상을 다시 생성했습니다."}


@app.post("/api/youtube/edit")
@app.post("/api/youtube/live/edit", deprecated=True)
async def edit_live_youtube(
    request: LiveEditRequest,
):
    """Create a Gemini-ranked highlight video from an uploaded YouTube VOD."""

    try:
        vod_id = extract_video_id(request.vod_url)
        archive_key = f"vod-{vod_id}"
        archive_path = _chat_archive_path(archive_key)

        # Replay chat is optional. Its absence must not block transcript-led
        # editing of an already uploaded video.
        if not archive_path.exists() or archive_path.stat().st_size == 0:
            try:
                await asyncio.to_thread(collect_chat_replay, vod_id, archive_key)
            except LiveYouTubeError:
                pass

        pipeline = LiveEditPipeline(get_media_root())
        result = await asyncio.to_thread(
            pipeline.run,
            vod_url=request.vod_url,
            archive_path=archive_path,
            genre=request.genre,
            actual_start_time=None,
            target_seconds=request.target_duration_seconds,
            chat_delay_seconds=request.chat_delay_seconds,
            clean_subtitles=request.clean_subtitles,
            delay_seconds=0.0,
            subtitle_offset_seconds=request.subtitle_offset_seconds,
            subtitle_font_name=request.subtitle_font_name,
            subtitle_font_size=request.subtitle_font_size,
            render_mode=request.render_mode,
        )
        result["vod_video_id"] = vod_id
        result["chat_replay_used"] = archive_path.exists() and archive_path.stat().st_size > 0
        return result
    except (LiveYouTubeError, LiveEditPipelineError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI 영상 편집에 실패했습니다: {exc}") from exc


@app.post("/api/youtube/live/finalize", response_model=LiveFinalizeResponse)
async def finalize_live_youtube(
    request: LiveFinalizeRequest,
    youtube_access_token: str = Header(..., alias="X-YouTube-Access-Token"),
):
    """Attach a completed VOD URL to the chat captured during the live event."""

    try:
        vod_id = extract_video_id(request.vod_url)
        vod = get_video_metadata(youtube_access_token, vod_id)
        archive_key = request.live_chat_id or f"vod-{vod_id}"
        archive_path = _chat_archive_path(archive_key)
        replay_warning = None

        # Prefer replay timestamps from pytchat. If it is unavailable or the
        # VOD has no replay, retain the official live-capture fallback.
        try:
            replay_result = await asyncio.to_thread(
                collect_chat_replay,
                vod_id,
                archive_key,
            )
        except LiveYouTubeError as exc:
            replay_result = None
            replay_warning = str(exc)

        has_archived_messages = archive_path.exists() and archive_path.stat().st_size > 0
        if not has_archived_messages:
            if replay_warning:
                raise LiveYouTubeError(
                    f"다시보기 채팅을 가져오지 못했고 저장된 라이브 채팅도 없습니다: {replay_warning}"
                )
            raise LiveYouTubeError("다시보기 채팅 데이터가 없습니다.")

        analysis = analyze_chat_archive(
            archive_key,
            actual_start_time=request.actual_start_time,
            duration_seconds=vod.get("duration_seconds"),
            bucket_seconds=request.bucket_seconds,
            delay_seconds=request.delay_seconds,
        )
        previous_session = _read_chat_session(archive_key)
        session = {
            "live_chat_id": archive_key,
            "actual_start_time": request.actual_start_time or previous_session.get("actual_start_time"),
            "vod_url": request.vod_url,
            "vod_video_id": vod_id,
            "vod_title": vod.get("title"),
            "vod_duration_iso": vod.get("duration_iso"),
            "chat_source": replay_result["source"] if replay_result else "live_capture_fallback",
            "chat_warning": replay_warning,
            "analysis": analysis,
        }
        _write_chat_session(archive_key, session)
        return {
            "vod_video_id": vod_id,
            "vod_title": vod.get("title"),
            "vod_duration_iso": vod.get("duration_iso"),
            "chat_file_path": str(archive_path.resolve()),
            "analysis": analysis,
            "source_video_required": True,
            "message": (
                "채팅 분석이 완료되었습니다. pytchat 리플레이 시간을 기준으로 "
                "하이라이트 후보를 생성했습니다."
            ),
        }
    except LiveYouTubeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"다시보기 연결에 실패했습니다: {exc}") from exc

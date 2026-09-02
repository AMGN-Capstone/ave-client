from __future__ import annotations

import json
import asyncio
import re
import requests
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_ave_server_url, get_database_root, get_media_root
from app.schemas import (
    AuthUserResponse,
    PublicConfigResponse,
    UploadVideoResponse,
    LiveChatResponse,
    LiveYouTubeRequest,
    YouTubeMetadataRequest,
    LiveFinalizeRequest,
    LiveFinalizeResponse,
    LiveEditRequest,
    SegmentSelectionRequest,
    SubtitleUpdateRequest,
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
from app.services.local_job_store import LocalJobStore
from app.services.server_job_service import ServerJobError, create_job as create_server_job, save_result as save_server_result, update_job as update_server_job


SUPPORTED_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
CHUNK_SIZE = 1024 * 1024
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
REACT_UI_DIR = STATIC_DIR / "ui"


app = FastAPI(title="Automatic Video Editor MVP")
auth_scheme = HTTPBearer(auto_error=False)
LIVE_EDIT_JOBS: dict[str, dict] = {}
EDIT_JOB_LOCKS: dict[str, asyncio.Lock] = {}

if (REACT_UI_DIR / "assets").exists():
    app.mount("/ui/assets", StaticFiles(directory=REACT_UI_DIR / "assets"), name="react-ui-assets")


def _user_value(user, key: str):
    if isinstance(user, dict):
        return user.get(key)
    return getattr(user, key, None)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Google login is required.")
    try:
        response = requests.get(
            f"{_server_url()}/api/auth/me",
            headers={"Authorization": f"Bearer {credentials.credentials}"},
            timeout=15,
        )
        response.raise_for_status()
        user = response.json()
        user_id = user.get("id") if isinstance(user, dict) else None
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired login session.") from exc

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid login session.")
    return user


def _server_url() -> str:
    value = get_ave_server_url()
    if not value.startswith("https://"):
        raise HTTPException(status_code=503, detail="AVE_SERVER_URL is not configured.")
    return value


@app.get("/")
async def index():
    index_path = REACT_UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="React UI를 빌드하세요: ui에서 npm run build")
    return FileResponse(index_path)


@app.get("/ui")
@app.get("/ui/")
async def react_index():
    index_path = REACT_UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="React UI를 빌드하세요: ui에서 npm run build")
    return FileResponse(index_path)


@app.get("/api/config", response_model=PublicConfigResponse)
async def public_config():
    try:
        response = requests.get(f"{_server_url()}/api/auth/config", timeout=15)
        response.raise_for_status()
        value = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(status_code=503, detail="AVE 서버 인증 설정을 불러오지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=503, detail="AVE 서버 인증 설정 응답이 올바르지 않습니다.")
    return value


@app.get("/api/auth/me", response_model=AuthUserResponse)
async def auth_me(user=Depends(get_current_user)):
    return {"id": str(_user_value(user, "id")), "email": _user_value(user, "email")}


@app.post("/api/videos/upload", response_model=UploadVideoResponse)
async def upload_video(file: UploadFile = File(...)):
    original_filename = Path(file.filename or "").name
    extension = Path(original_filename).suffix.lower()

    if extension not in SUPPORTED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported video format.")

    upload_id = uuid4().hex
    upload_dir = get_media_root() / "yt-edit" / "uploads" / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored_filename = f"{upload_id}{extension}"
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
        LocalJobStore(get_database_root()).create_or_update_state(job_id, job)


async def _run_live_edit_job(
    job_id: str,
    request: LiveEditRequest,
    server_access_token: str | None = None,
) -> None:
    server_job_id: str | None = None
    try:
        _update_live_edit_job(job_id, status="running", progress=3, message="업로드된 영상을 확인하는 중입니다.")
        vod_id = extract_video_id(request.vod_url)
        server_job_id = await asyncio.to_thread(create_server_job, server_access_token or "", client_job_id=job_id, source_id=vod_id, source_url=request.vod_url)
        LIVE_EDIT_JOBS[job_id]["server_job_id"] = server_job_id
        await asyncio.to_thread(update_server_job, server_access_token or "", server_job_id, status="collecting", progress=3)
        archive_path = _chat_archive_path(job_id)

        if not archive_path.exists() or archive_path.stat().st_size == 0:
            _update_live_edit_job(job_id, progress=8, message="지원되는 채팅 리플레이를 확인하는 중입니다.")
            try:
                await asyncio.to_thread(collect_chat_replay, vod_id, job_id)
            except LiveYouTubeError:
                # Chat replay is an optional signal. A completed video without
                # replay chat must still be editable from its transcript.
                pass

        pipeline = LiveEditPipeline(get_media_root(), database_root=get_database_root())
        result = await asyncio.to_thread(
            pipeline.run,
            job_id=job_id,
            vod_url=request.vod_url,
            archive_path=archive_path,
            genre=request.genre,
            llm_provider=request.llm_provider,
            actual_start_time=None,
            target_seconds=request.target_duration_seconds,
            chat_delay_seconds=request.chat_delay_seconds,
            clean_subtitles=request.clean_subtitles,
            transcription_source=request.transcription_source,
            stt_language=request.stt_language,
            stt_initial_prompt=request.stt_initial_prompt,
            stt_hotwords=request.stt_hotwords,
            stt_speed=request.stt_speed,
            delay_seconds=0.0,
            subtitle_offset_seconds=request.subtitle_offset_seconds,
            subtitle_font_name=request.subtitle_font_name,
            subtitle_font_size=request.subtitle_font_size,
            render_mode=request.render_mode,
            defer_render=request.interactive_selection,
            server_access_token=server_access_token,
            progress_callback=lambda progress, message: _update_live_edit_job(
                job_id, progress=progress, message=message
            ),
        )
        result["vod_video_id"] = vod_id
        result["chat_replay_used"] = archive_path.exists() and archive_path.stat().st_size > 0
        await asyncio.to_thread(save_server_result, server_access_token or "", server_job_id, result)
        if result.get("awaiting_selection"):
            await asyncio.to_thread(update_server_job, server_access_token or "", server_job_id, status="analyzing", progress=100)
            _update_live_edit_job(
                job_id,
                status="awaiting_selection",
                progress=100,
                phase="selection",
                message="AI 분석이 완료되었습니다. 원하는 구간을 선택하세요.",
                result=result,
            )
        else:
            await asyncio.to_thread(update_server_job, server_access_token or "", server_job_id, status="completed", progress=100)
            _update_live_edit_job(
                job_id,
                status="completed",
                progress=100,
                phase="render",
                message="AI 영상 편집이 완료되었습니다.",
                result=result,
            )
    except (LiveYouTubeError, LiveEditPipelineError, ServerJobError) as exc:
        if server_job_id:
            try:
                await asyncio.to_thread(update_server_job, server_access_token or "", server_job_id, status="failed", progress=100, error_message=str(exc))
            except ServerJobError:
                pass
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
    authorization: str | None = Header(default=None),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="AVE 서버 연동에는 로그인 토큰이 필요합니다.")
    job_id = uuid4().hex
    LIVE_EDIT_JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "phase": "analysis",
        "message": "AI 편집 작업을 준비하는 중입니다.",
    }
    LocalJobStore(get_database_root()).create_or_update_state(job_id, LIVE_EDIT_JOBS[job_id])
    asyncio.create_task(_run_live_edit_job(job_id, request, authorization))
    return LIVE_EDIT_JOBS[job_id]


@app.post("/api/youtube/metadata")
async def youtube_metadata(request: YouTubeMetadataRequest):
    """Return the phase-one preview data using yt-dlp only."""
    try:
        extract_video_id(request.url)
        return await asyncio.to_thread(get_video_metadata, request.url)
    except LiveYouTubeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/youtube/thumbnail/{video_id}/{filename}")
async def youtube_thumbnail(video_id: str, filename: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="썸네일을 찾을 수 없습니다.")
    path = get_media_root() / "yt-data" / video_id / "thumbnails" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="썸네일을 찾을 수 없습니다.")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/youtube/edit/status/{job_id}")
@app.get("/api/youtube/live/edit/status/{job_id}", deprecated=True)
async def live_edit_status(job_id: str):
    job = LIVE_EDIT_JOBS.get(job_id)
    if job is None:
        job = LocalJobStore(get_database_root()).get_state(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="AI 편집 작업을 찾을 수 없습니다.")
    return job


async def _run_segment_selection_job(
    job_id: str,
    request: SegmentSelectionRequest,
    server_access_token: str | None = None,
) -> None:
    lock = EDIT_JOB_LOCKS.setdefault(job_id, asyncio.Lock())
    async with lock:
        previous_result = dict(LIVE_EDIT_JOBS.get(job_id, {}).get("result") or {})
        server_job_id = LIVE_EDIT_JOBS.get(job_id, {}).get("server_job_id")
        try:
            _update_live_edit_job(
                job_id,
                status="running",
                progress=0,
                phase="render",
                message="사용자가 선택한 구간으로 편집을 준비하는 중입니다.",
            )
            pipeline = LiveEditPipeline(get_media_root(), database_root=get_database_root())
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
            if server_job_id:
                await asyncio.to_thread(save_server_result, server_access_token or "", server_job_id, previous_result, selection={"selected_segment_ids": request.segment_ids, "feedback": request.feedback})
                await asyncio.to_thread(update_server_job, server_access_token or "", server_job_id, status="completed", progress=100)
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
    directory = get_media_root() / "yt-edit" / job_id
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="편집 작업을 찾을 수 없습니다.")
    return directory


def _edit_media_response(output_dir: Path, plan: dict, kind: str):
    if kind == "source":
        media_path = Path(str(plan.get("source_video_path", "")))
        if not media_path.is_absolute():
            media_path = (Path.cwd() / media_path).resolve()
    elif kind == "rendered":
        render_mode = str(plan.get("render_mode", "preview"))
        filename = str(plan.get("rendered_filename") or f"edited-{render_mode}.mp4")
        if Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="잘못된 영상 경로입니다.")
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


@app.get("/api/youtube/edit/{job_id}/segments")
@app.get("/api/youtube/live/edit/{job_id}/segments", deprecated=True)
async def get_edit_segments(job_id: str):
    _edit_output_dir(job_id)
    pipeline = LiveEditPipeline(get_media_root(), database_root=get_database_root())
    try:
        return pipeline.get_segment_review(job_id)
    except LiveEditPipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/youtube/edit/{job_id}/segments", status_code=202)
@app.put("/api/youtube/live/edit/{job_id}/segments", status_code=202, deprecated=True)
async def update_edit_segments(job_id: str, request: SegmentSelectionRequest, authorization: str | None = Header(default=None)):
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
    if LIVE_EDIT_JOBS.get(job_id, {}).get("server_job_id") and not authorization:
        raise HTTPException(status_code=401, detail="서버 작업 동기화에는 로그인 토큰이 필요합니다.")
    asyncio.create_task(_run_segment_selection_job(job_id, request, authorization))
    return LIVE_EDIT_JOBS[job_id]


@app.get("/api/youtube/edit/{job_id}/media/{kind}")
@app.get("/api/youtube/live/edit/{job_id}/media/{kind}", deprecated=True)
async def get_edit_media(job_id: str, kind: str):
    output_dir = _edit_output_dir(job_id)
    stored = LocalJobStore(get_database_root()).get_analysis(job_id)
    if stored is not None:
        return _edit_media_response(output_dir, stored["plan"], kind)
    raise HTTPException(status_code=404, detail="SQLite에 저장된 편집 작업을 찾을 수 없습니다.")
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
    pipeline = LiveEditPipeline(get_media_root(), database_root=get_database_root())
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
        job_id = uuid4().hex
        archive_path = _chat_archive_path(job_id)

        # Replay chat is optional. Its absence must not block transcript-led
        # editing of an already uploaded video.
        if not archive_path.exists() or archive_path.stat().st_size == 0:
            try:
                await asyncio.to_thread(collect_chat_replay, vod_id, job_id)
            except LiveYouTubeError:
                pass

        pipeline = LiveEditPipeline(get_media_root(), database_root=get_database_root())
        result = await asyncio.to_thread(
            pipeline.run,
            job_id=job_id,
            vod_url=request.vod_url,
            archive_path=archive_path,
            genre=request.genre,
            llm_provider=request.llm_provider,
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
):
    """Attach a completed VOD URL to the chat captured during the live event."""

    try:
        vod_id = extract_video_id(request.vod_url)
        vod = await asyncio.to_thread(get_video_metadata, request.vod_url)
        archive_key = request.live_chat_id or f"vod-{vod_id}"
        archive_path = _chat_archive_path(archive_key)
        replay_warning = None

        # Prefer replay timestamps from yt-dlp. If it is unavailable or the
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
                "채팅 분석이 완료되었습니다. yt-dlp 리플레이 시간을 기준으로 "
                "하이라이트 후보를 생성했습니다."
            ),
        }
    except LiveYouTubeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"다시보기 연결에 실패했습니다: {exc}") from exc

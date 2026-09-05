from __future__ import annotations

import json
import asyncio
import re
import requests
import time
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_ave_server_url, get_database_root, get_media_root
from app.schemas import (
    AuthUserResponse,
    PublicConfigResponse,
    YouTubeMetadataRequest,
    YouTubeMetadataMaterialsRequest,
    LiveEditRequest,
    SegmentSelectionRequest,
)
from app.services.live_youtube_service import (
    LiveYouTubeError,
    extract_video_id,
    get_video_metadata,
    download_metadata_materials,
)
from app.services.live_edit_pipeline import LiveEditCancelled, LiveEditPipeline, LiveEditPipelineError
from app.services.local_job_store import LocalJobStore
from app.services.server_job_service import ServerJobError, create_job as create_server_job, save_result as save_server_result
from app.services.server_media_service import ServerMediaError, cancel_pending_uploaded_transcription, cancel_uploaded_transcription


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
REACT_UI_DIR = STATIC_DIR / "ui"


app = FastAPI(title="Automatic Video Editor MVP")
auth_scheme = HTTPBearer(auto_error=False)
LIVE_EDIT_JOBS: dict[str, dict] = {}
LIVE_EDIT_CANCEL_REQUESTS: set[str] = set()
METADATA_MATERIAL_JOBS: dict[str, dict] = {}
LIVE_EDIT_ACCESS_TOKENS: dict[str, str] = {}
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


def _new_edit_job_id(vod_url: str) -> str:
    """Use a source-stable, filesystem-safe job ID without retaining counters."""

    return f"{extract_video_id(vod_url)}.{int(time.time() * 1000):x}"


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


@app.get("/ui/favicon.svg")
async def react_favicon():
    favicon_path = REACT_UI_DIR / "favicon.svg"
    if not favicon_path.exists():
        raise HTTPException(status_code=404, detail="React UI 파비콘을 찾을 수 없습니다.")
    return FileResponse(
        favicon_path,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


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


def _update_live_edit_job(job_id: str, **values) -> None:
    job = LIVE_EDIT_JOBS.get(job_id)
    if job is not None:
        job.update(values)


def _raise_if_cancel_requested(job_id: str) -> None:
    if job_id in LIVE_EDIT_CANCEL_REQUESTS or LIVE_EDIT_JOBS.get(job_id, {}).get("status") == "cancel_requested":
        raise LiveEditCancelled("사용자가 작업을 취소했습니다.")


def _completed_job(job_id: str) -> dict | None:
    return LocalJobStore(get_database_root()).get_completed(job_id)


async def _discard_failed_edit_job(job_id: str, error: Exception) -> None:
    """Notify the connected browser briefly, then remove a failed job completely."""

    detail = str(error).strip() or "알 수 없는 오류"
    job = LIVE_EDIT_JOBS.get(job_id)
    if job is not None:
        _update_live_edit_job(
            job_id,
            status="failed",
            message=f"AI 편집 작업이 실패했습니다: {detail}",
            error=detail,
        )
        # 실패 이력은 저장하지 않지만, SSE가 마지막 오류를 브라우저에 보낼 짧은
        # 시간은 필요하다. 이후에는 작업 폴더·메모리·토큰을 모두 제거한다.
        await asyncio.sleep(1.2)
    shutil.rmtree(get_media_root() / "yt-edit" / job_id, ignore_errors=True)
    LIVE_EDIT_JOBS.pop(job_id, None)
    LIVE_EDIT_ACCESS_TOKENS.pop(job_id, None)
    LIVE_EDIT_CANCEL_REQUESTS.discard(job_id)


async def _run_live_edit_job(
    job_id: str,
    request: LiveEditRequest,
    server_access_token: str | None = None,
) -> None:
    server_job_id: str | None = None

    def report_analysis(progress: int, message: str) -> None:
        _raise_if_cancel_requested(job_id)
        _update_live_edit_job(job_id, progress=progress, phase="analysis", message=message)

    def report_transcription(progress: int, message: str) -> None:
        _raise_if_cancel_requested(job_id)
        _update_live_edit_job(
            job_id,
            progress=progress,
            transcription_progress=progress,
            phase="transcription",
            message=message,
        )

    def report_whisper_preparing() -> None:
        _raise_if_cancel_requested(job_id)
        _update_live_edit_job(job_id, whisper_preparing=True, phase="transcription", message="Whisper 전사를 준비하는 중입니다.")

    try:
        _update_live_edit_job(job_id, status="running", progress=3, message="2단계에서 준비한 영상 자료를 확인하는 중입니다.")
        vod_id = extract_video_id(request.vod_url)

        pipeline = LiveEditPipeline(get_media_root())
        result = await asyncio.to_thread(
            pipeline.run,
            job_id=job_id,
            vod_url=request.vod_url,
            genre=request.genre,
            llm_provider=request.llm_provider,
            target_seconds=request.target_duration_seconds,
            transcription_source=request.transcription_source,
            transcript_language=request.transcript_language,
            stt_language=request.stt_language,
            stt_initial_prompt=request.stt_initial_prompt,
            stt_hotwords=request.stt_hotwords,
            stt_speed=request.stt_speed,
            subtitle_font_name=request.subtitle_font_name,
            subtitle_font_size=request.subtitle_font_size,
            render_mode=request.render_mode,
            defer_render=True,
            server_access_token=server_access_token,
            server_job_id=server_job_id,
            progress_callback=report_analysis,
            cancel_callback=lambda: _raise_if_cancel_requested(job_id),
            whisper_progress_callback=report_transcription,
            whisper_preparing_callback=report_whisper_preparing,
            whisper_job_started_callback=lambda runpod_job_id: _update_live_edit_job(
                job_id, runpod_job_id=runpod_job_id, phase="transcription", message="Whisper 전사 작업을 시작했습니다."
            ),
        )
        result["vod_video_id"] = vod_id
        if result.get("awaiting_selection"):
            _update_live_edit_job(
                job_id,
                status="awaiting_selection",
                progress=100,
                phase="selection",
                message="AI 분석이 완료되었습니다. 원하는 구간을 선택하세요.",
                result=result,
            )
            LIVE_EDIT_CANCEL_REQUESTS.discard(job_id)
        else:
            server_job_id = await asyncio.to_thread(create_server_job, server_access_token or "", client_job_id=job_id, source_id=vod_id, source_url=request.vod_url)
            await asyncio.to_thread(save_server_result, server_access_token or "", server_job_id, result)
            LocalJobStore(get_database_root()).save_completed(job_id, result)
            _update_live_edit_job(
                job_id,
                status="completed",
                progress=100,
                phase="render",
                message="AI 영상 편집이 완료되었습니다.",
                result=result,
            )
    except LiveEditCancelled as exc:
        shutil.rmtree(get_media_root() / "yt-edit" / job_id, ignore_errors=True)
        LIVE_EDIT_JOBS.pop(job_id, None)
        LIVE_EDIT_ACCESS_TOKENS.pop(job_id, None)
        LIVE_EDIT_CANCEL_REQUESTS.discard(job_id)
    except (LiveYouTubeError, LiveEditPipelineError, ServerJobError) as exc:
        await _discard_failed_edit_job(job_id, exc)
    except Exception as exc:
        await _discard_failed_edit_job(job_id, exc)


@app.post("/api/youtube/edit/start", status_code=202)
async def start_live_edit(
    request: LiveEditRequest,
    authorization: str | None = Header(default=None),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="AVE 서버 연동에는 로그인 토큰이 필요합니다.")
    try:
        job_id = _new_edit_job_id(request.vod_url)
    except LiveYouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    LIVE_EDIT_JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "phase": "analysis",
        "message": "AI 편집 작업을 준비하는 중입니다.",
        "transcription_source": request.transcription_source,
    }
    LIVE_EDIT_ACCESS_TOKENS[job_id] = authorization
    asyncio.create_task(_run_live_edit_job(job_id, request, authorization))
    return LIVE_EDIT_JOBS[job_id]


@app.post("/api/youtube/metadata")
async def youtube_metadata(request: YouTubeMetadataRequest):
    """Return the phase-one preview data using yt-dlp only."""
    try:
        extract_video_id(request.url)
        # 같은 영상의 재작업은 yt-data 원본 정보를 그대로 다시 보여 준다.
        return await asyncio.to_thread(get_video_metadata, request.url, refresh=False)
    except LiveYouTubeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/youtube/metadata/materials")
async def youtube_metadata_materials(request: YouTubeMetadataMaterialsRequest):
    try:
        extract_video_id(request.url)
        selections = {key: getattr(request, key) for key in ("comments", "chat", "subtitles", "captions", "subtitle_language", "caption_language")}
        return await asyncio.to_thread(download_metadata_materials, request.url, selections)
    except LiveYouTubeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _run_metadata_material_job(job_id: str, request: YouTubeMetadataMaterialsRequest) -> None:
    job = METADATA_MATERIAL_JOBS[job_id]

    def update(progress: int, message: str) -> None:
        job.update({"progress": max(0, min(100, progress)), "message": message})

    try:
        selections = {key: getattr(request, key) for key in ("comments", "chat", "subtitles", "captions", "subtitle_language", "caption_language")}
        result = await asyncio.to_thread(download_metadata_materials, request.url, selections, update)
        job.update({"status": "completed", "progress": 100, "message": "추가 메타데이터 준비를 완료했습니다.", "result": result})
    except LiveYouTubeError as exc:
        job.update({"status": "failed", "message": str(exc), "error": str(exc)})
    except Exception as exc:
        job.update({"status": "failed", "message": "추가 메타데이터 다운로드에 실패했습니다.", "error": str(exc)})


@app.post("/api/youtube/metadata/materials/start", status_code=202)
async def start_youtube_metadata_materials(request: YouTubeMetadataMaterialsRequest):
    try:
        extract_video_id(request.url)
    except LiveYouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_id = uuid4().hex
    METADATA_MATERIAL_JOBS[job_id] = {"job_id": job_id, "status": "running", "progress": 0, "message": "추가 메타데이터 다운로드를 준비하는 중입니다."}
    asyncio.create_task(_run_metadata_material_job(job_id, request))
    return METADATA_MATERIAL_JOBS[job_id]


@app.get("/api/youtube/metadata/materials/{job_id}")
async def get_youtube_metadata_material_job(job_id: str):
    job = METADATA_MATERIAL_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="추가 메타데이터 작업을 찾을 수 없습니다.")
    response = dict(job)
    # 결과 파일은 보존하지만, UI가 끝 상태를 읽은 뒤 진행 상태는 남기지 않는다.
    if response.get("status") in {"completed", "failed"}:
        METADATA_MATERIAL_JOBS.pop(job_id, None)
    return response


@app.get("/api/youtube/thumbnail/{video_id}/{filename}")
async def youtube_thumbnail(video_id: str, filename: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="썸네일을 찾을 수 없습니다.")
    path = get_media_root() / "yt-data" / video_id / "thumbnails" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="썸네일을 찾을 수 없습니다.")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/youtube/edit/status/{job_id}")
async def live_edit_status(job_id: str):
    job = LIVE_EDIT_JOBS.get(job_id) or _completed_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="AI 편집 작업을 찾을 수 없습니다.")
    return job


@app.get("/api/youtube/edit/{job_id}/events")
async def live_edit_events(job_id: str):
    """로컬 작업 상태를 브라우저에 SSE로 전달한다."""
    async def events():
        previous = ""
        yield "retry: 1000\n\n"
        while True:
            job = LIVE_EDIT_JOBS.get(job_id) or _completed_job(job_id)
            if job is None:
                yield 'event: error\ndata: {"detail":"AI 편집 작업을 찾을 수 없습니다."}\n\n'
                return
            payload = json.dumps(job, ensure_ascii=False)
            if payload != previous:
                yield f"data: {payload}\n\n"
                previous = payload
            # 선택 대기 상태는 4단계 진입에 필요한 마지막 이벤트다. 스트림을
            # 즉시 닫으면 프록시 버퍼가 이 이벤트를 버릴 수 있으므로 heartbeat를
            # 유지한다. 완료 뒤에는 클라이언트가 연결을 닫는다.
            if job.get("status") in {"completed", "failed", "cancelled"}:
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.get("/api/youtube/edit/active")
async def active_live_edit_jobs():
    terminal = {"completed", "failed", "cancelled"}
    return {"jobs": [job for job in LIVE_EDIT_JOBS.values() if job.get("status") not in terminal]}


@app.post("/api/youtube/edit/{job_id}/cancel")
async def cancel_live_edit(job_id: str, authorization: str | None = Header(default=None)):
    if not job_id or Path(job_id).name != job_id:
        raise HTTPException(status_code=400, detail="잘못된 편집 작업 ID입니다.")
    # 취소는 이미 정리된 실패 작업에도 멱등적으로 성공해야 하므로, 여기서는
    # 작업 폴더의 존재를 요구하지 않는다.
    output_dir = get_media_root() / "yt-edit" / job_id
    is_live = job_id in LIVE_EDIT_JOBS
    job = LIVE_EDIT_JOBS.get(job_id)
    if job is None:
        # 실패한 작업은 이력 없이 즉시 메모리와 작업 폴더에서 지운다. 브라우저가
        # 뒤늦게 보낸 취소 요청은 이미 정리된 상태로 간주해 성공으로 응답한다.
        shutil.rmtree(output_dir, ignore_errors=True)
        LIVE_EDIT_ACCESS_TOKENS.pop(job_id, None)
        return {"job_id": job_id, "status": "cancelled", "message": "작업은 이미 종료되어 임시 파일을 정리했습니다."}
    if job.get("status") in {"completed", "awaiting_selection"}:
        return job
    LIVE_EDIT_CANCEL_REQUESTS.add(job_id)
    runpod_job_id = job.get("runpod_job_id")
    if isinstance(runpod_job_id, str) and runpod_job_id and not authorization:
        raise HTTPException(status_code=401, detail="Whisper 작업 취소에는 로그인 토큰이 필요합니다.")
    job = {
        **job,
        "status": "cancel_requested" if is_live else "cancelled",
        "progress": job.get("progress", 0) if is_live else 100,
        "phase": "cancelled",
        "message": "작업 취소를 요청했습니다." if is_live else "실행 중이 아닌 이전 작업을 정리했습니다.",
        "error": None,
    }
    if is_live:
        LIVE_EDIT_JOBS[job_id] = job
    access_token = authorization or LIVE_EDIT_ACCESS_TOKENS.get(job_id)
    if job.get("transcription_source") == "whisper_api" and job.get("whisper_preparing") and not runpod_job_id and access_token:
        try:
            await asyncio.to_thread(cancel_pending_uploaded_transcription, job_id, access_token)
        except ServerMediaError as exc:
            job["message"] = f"취소를 요청했습니다. 서버 확인을 다시 시도합니다: {exc}"
            if is_live:
                LIVE_EDIT_JOBS[job_id] = job
    if isinstance(runpod_job_id, str) and runpod_job_id:
        try:
            await asyncio.to_thread(cancel_uploaded_transcription, runpod_job_id, access_token or "")
        except ServerMediaError as exc:
            # 서버가 이미 RunPod 취소를 시작했거나 일시적으로 응답하지 않을 수
            # 있으므로 로컬 상태는 유지한다. heartbeat 중단 뒤 server lease가 재시도한다.
            job["message"] = f"취소를 요청했습니다. 서버 확인을 다시 시도합니다: {exc}"
            if is_live:
                LIVE_EDIT_JOBS[job_id] = job
    shutil.rmtree(output_dir, ignore_errors=True)
    LIVE_EDIT_JOBS.pop(job_id, None)
    LIVE_EDIT_ACCESS_TOKENS.pop(job_id, None)
    return {"job_id": job_id, "status": "cancelled", "message": "작업을 취소하고 임시 파일을 정리했습니다."}


@app.post("/api/youtube/edit/cancel-active")
async def cancel_all_live_edit_jobs():
    """트레이 종료 시 이 프로세스가 보유한 작업을 모두 중단한다."""
    cancelled = 0
    terminal = {"completed", "failed", "cancelled"}
    jobs = dict(LIVE_EDIT_JOBS)
    for job_id, job in jobs.items():
        if job.get("status") in terminal:
            continue
        await cancel_live_edit(job_id, LIVE_EDIT_ACCESS_TOKENS.get(job_id))
        cancelled += 1
    return {"cancelled": cancelled}


async def _run_segment_selection_job(
    job_id: str,
    request: SegmentSelectionRequest,
    server_access_token: str | None = None,
) -> None:
    lock = EDIT_JOB_LOCKS.setdefault(job_id, asyncio.Lock())
    async with lock:
        previous_result = dict(LIVE_EDIT_JOBS.get(job_id, {}).get("result") or {})

        def report_render(progress: int, message: str) -> None:
            _raise_if_cancel_requested(job_id)
            _update_live_edit_job(job_id, progress=progress, message=message)

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
                plan=dict(previous_result.get("analysis_plan") or {}),
                progress_callback=report_render,
            )
            final_plan = dict(previous_result.get("analysis_plan") or {})
            final_plan.update({
                "clips": [
                    {key: item[key] for key in ("segment_id", "start", "end", "llm_score") if key in item}
                    for item in (result.get("segments") or []) if item.get("selected")
                ],
                "selected_segment_ids": result.get("selected_segment_ids") or request.segment_ids,
                "rendered_filename": result.get("rendered_filename"),
            })
            merged_result = {
                **previous_result,
                **result,
                "analysis_plan": final_plan,
                "awaiting_selection": False,
            }
            # 렌더링 성공은 로컬 완료의 기준이다. 서버 이력 동기화 실패가 이미
            # 생성된 결과 영상까지 폐기하게 해서는 안 된다.
            LocalJobStore(get_database_root()).save_completed(job_id, merged_result)
            vod_url = str(previous_result.get("vod_url") or "")
            sync_warning = None
            if server_access_token and vod_url:
                try:
                    vod_id = extract_video_id(vod_url)
                    server_job_id = await asyncio.to_thread(create_server_job, server_access_token, client_job_id=job_id, source_id=vod_id, source_url=vod_url)
                    await asyncio.to_thread(save_server_result, server_access_token, server_job_id, merged_result, selection={"selected_segment_ids": request.segment_ids})
                except ServerJobError as exc:
                    sync_warning = str(exc)
            _update_live_edit_job(
                job_id,
                status="completed",
                progress=100,
                phase="render",
                message="선택한 구간으로 영상을 다시 만들었습니다." if not sync_warning else f"영상 생성은 완료됐지만 {sync_warning}",
                result=merged_result,
                error=None,
            )
        except LiveEditCancelled:
            shutil.rmtree(get_media_root() / "yt-edit" / job_id, ignore_errors=True)
            LIVE_EDIT_JOBS.pop(job_id, None)
            LIVE_EDIT_ACCESS_TOKENS.pop(job_id, None)
        except LiveEditPipelineError as exc:
            await _discard_failed_edit_job(job_id, exc)
        except Exception as exc:
            await _discard_failed_edit_job(job_id, exc)


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
async def get_edit_segments(job_id: str):
    current = LIVE_EDIT_JOBS.get(job_id)
    plan = (current or {}).get("result", {}).get("analysis_plan")
    if not isinstance(plan, dict):
        raise HTTPException(status_code=404, detail="선택 대기 중인 분석 작업을 찾을 수 없습니다.")
    pipeline = LiveEditPipeline(get_media_root())
    try:
        return pipeline.get_segment_review(job_id, plan)
    except LiveEditPipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/youtube/edit/{job_id}/segments", status_code=202)
async def update_edit_segments(job_id: str, request: SegmentSelectionRequest, authorization: str | None = Header(default=None)):
    current = LIVE_EDIT_JOBS.get(job_id)
    if not current or not isinstance((current.get("result") or {}).get("analysis_plan"), dict):
        raise HTTPException(status_code=404, detail="선택 대기 중인 분석 작업을 찾을 수 없습니다.")
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
    if authorization:
        LIVE_EDIT_ACCESS_TOKENS[job_id] = authorization
    asyncio.create_task(_run_segment_selection_job(job_id, request, authorization))
    return LIVE_EDIT_JOBS[job_id]


@app.get("/api/youtube/edit/{job_id}/media/{kind}")
async def get_edit_media(job_id: str, kind: str):
    output_dir = _edit_output_dir(job_id)
    active = LIVE_EDIT_JOBS.get(job_id, {})
    result = active.get("result") or (_completed_job(job_id) or {}).get("result") or {}
    if kind == "source":
        source = Path(str((active.get("result") or {}).get("analysis_plan", {}).get("source_video_path", "")))
        if source.is_file():
            return FileResponse(source, media_type="video/mp4", headers={"Cache-Control": "no-store"})
    if kind != "rendered":
        raise HTTPException(status_code=404, detail="원본 미리보기 단계가 종료되었습니다.")
    filename = Path(str(result.get("rendered_filename") or "")).name
    if not filename:
        raise HTTPException(status_code=404, detail="완료된 결과 영상을 찾을 수 없습니다.")
    media_path = output_dir / filename
    if not media_path.is_file():
        raise HTTPException(status_code=404, detail="영상 파일을 찾을 수 없습니다.")
    return FileResponse(media_path, media_type="video/mp4", headers={"Cache-Control": "no-store"})

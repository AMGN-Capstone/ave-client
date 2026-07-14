from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
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


SUPPORTED_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
CHUNK_SIZE = 1024 * 1024
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


app = FastAPI(title="Longform Auto Editor MVP")
auth_scheme = HTTPBearer(auto_error=False)

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

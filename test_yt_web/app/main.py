from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_media_root
from app.schemas import UploadVideoResponse, YouTubeImportRequest, YouTubeImportResponse
from app.services.youtube_importer import (
    InvalidYouTubeURLError,
    YouTubeImporter,
    YouTubeImportError,
)


SUPPORTED_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
CHUNK_SIZE = 1024 * 1024
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


app = FastAPI(title="Longform Auto Editor MVP")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_youtube_importer() -> YouTubeImporter:
    return YouTubeImporter(get_media_root())


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend has not been built.")
    return FileResponse(index_path)


@app.post("/api/youtube/import", response_model=YouTubeImportResponse)
async def import_youtube_video(
    request: YouTubeImportRequest,
    importer: YouTubeImporter = Depends(get_youtube_importer),
):
    try:
        return await importer.import_video(request.url)
    except InvalidYouTubeURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except YouTubeImportError as exc:
        raise HTTPException(status_code=502, detail=f"YouTube import failed: {exc}") from exc


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

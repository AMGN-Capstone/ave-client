from pydantic import BaseModel, Field


class YouTubeImportRequest(BaseModel):
    url: str = Field(..., min_length=1)


class YouTubeImportResponse(BaseModel):
    job_id: str
    source_url: str
    title: str | None = None
    duration: int | None = None
    video_path: str | None = None
    subtitle_files: list[str]
    metadata_path: str
    warnings: list[str] = []


class UploadVideoResponse(BaseModel):
    original_filename: str
    stored_filename: str
    content_type: str | None = None
    size_bytes: int
    path: str

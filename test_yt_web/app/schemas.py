from __future__ import annotations

from pydantic import BaseModel, Field


class YouTubeImportRequest(BaseModel):
    url: str = Field(..., min_length=1)


class YouTubeImportResponse(BaseModel):
    job_id: str
    video_id: str | None = None
    transcript_id: str | None = None
    source_url: str
    title: str | None = None
    duration: int | None = None
    video_path: str | None = None
    subtitle_files: list[str]
    metadata_path: str
    warnings: list[str] = Field(default_factory=list)


class AuthUserResponse(BaseModel):
    id: str
    email: str | None = None


class PublicConfigResponse(BaseModel):
    supabase_url: str
    supabase_anon_key: str


class UploadVideoResponse(BaseModel):
    original_filename: str
    stored_filename: str
    content_type: str | None = None
    size_bytes: int
    path: str

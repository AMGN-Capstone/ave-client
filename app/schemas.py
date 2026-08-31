from __future__ import annotations

from typing import Literal

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


class LiveYouTubeRequest(BaseModel):
    url: str = Field(..., min_length=1)


class LiveChatResponse(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    next_page_token: str | None = None
    polling_interval_millis: int = 5000
    offline_at: str | None = None
    chat_file_path: str | None = None
    total_messages: int = 0
    highlight_windows: list[dict] = Field(default_factory=list)


class LiveFinalizeRequest(BaseModel):
    live_chat_id: str | None = Field(default=None, min_length=1)
    vod_url: str = Field(..., min_length=1)
    actual_start_time: str | None = None
    bucket_seconds: int = Field(default=30, ge=5, le=300)
    delay_seconds: float = Field(default=0.0, ge=0, le=120)


class LiveFinalizeResponse(BaseModel):
    vod_video_id: str
    vod_title: str | None = None
    vod_duration_iso: str | None = None
    chat_file_path: str | None = None
    analysis: dict
    source_video_required: bool = True
    message: str


class LiveEditRequest(BaseModel):
    vod_url: str = Field(..., min_length=1, description="이미 업로드된 YouTube 영상 URL")
    genre: Literal["ai_news", "stock", "game"] = "ai_news"
    target_duration_seconds: int = Field(default=600, ge=60, le=3600)
    chat_delay_seconds: float = Field(default=0.0, ge=-120, le=120)
    clean_subtitles: bool = Field(default=False, description="AI 자막 정제 실행 여부")
    subtitle_offset_seconds: float = Field(default=0.0, ge=-120, le=120)
    subtitle_font_name: str = Field(default="Malgun Gothic", min_length=1, max_length=100)
    subtitle_font_size: int = Field(default=18, ge=8, le=64)
    render_mode: str = Field(default="preview", pattern="^(preview|exact)$")
    interactive_selection: bool = True


class SubtitleUpdateRequest(BaseModel):
    content: str = Field(default="", max_length=2_000_000)


class SegmentSelectionRequest(BaseModel):
    segment_ids: list[str] = Field(..., min_length=1, max_length=500)
    feedback: str | None = Field(default=None, max_length=2_000)

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AuthUserResponse(BaseModel):
    id: str
    email: str | None = None


class PublicConfigResponse(BaseModel):
    supabase_url: str
    supabase_anon_key: str


class YouTubeMetadataRequest(BaseModel):
    url: str = Field(..., min_length=1, description="확인할 YouTube 영상 URL")


class YouTubeMetadataMaterialsRequest(YouTubeMetadataRequest):
    comments: bool = False
    chat: bool = False
    subtitles: bool = False
    captions: bool = False
    subtitle_language: str | None = Field(default=None, max_length=40)
    caption_language: str | None = Field(default=None, max_length=40)


class LiveEditRequest(BaseModel):
    vod_url: str = Field(..., min_length=1, description="이미 업로드된 YouTube 영상 URL")
    llm_provider: Literal["gemini", "deepseek"] = "deepseek"
    genre: Literal["ai_news", "stock", "game"] = "ai_news"
    target_duration_seconds: int = Field(default=600, ge=60, le=3600)
    transcription_source: Literal["youtube_caption", "youtube_subtitle", "whisper_api"] = "youtube_caption"
    transcript_language: str | None = Field(default=None, min_length=1, max_length=40)
    stt_language: str = Field(default="ko", min_length=1, max_length=20)
    stt_initial_prompt: str | None = Field(default=None, max_length=1_000)
    stt_hotwords: str | None = Field(default=None, max_length=1_000)
    stt_speed: Literal[1.0, 1.5, 2.0] = 1.0
    subtitle_font_name: str = Field(default="Malgun Gothic", min_length=1, max_length=100)
    subtitle_font_size: int = Field(default=18, ge=8, le=64)
    render_mode: str = Field(default="preview", pattern="^(preview|exact)$")

class SegmentSelectionRequest(BaseModel):
    segment_ids: list[str] = Field(..., min_length=1, max_length=500)

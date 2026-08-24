"""End-to-end local-video transcription and Gemini edit planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.gemini_editor import GeminiEditor
from app.services.local_video_transcriber import transcribe_video


def analyze_local_video(
    video_path: str | Path,
    *,
    whisper_model: str | None = None,
    language: str = "ko",
    whisper_device: str | None = None,
    whisper_compute_type: str | None = None,
    gemini_model: str | None = None,
) -> dict[str, Any]:
    transcript = transcribe_video(
        video_path,
        model_size=whisper_model,
        language=language,
        device=whisper_device,
        compute_type=whisper_compute_type,
    )
    edit_plan = GeminiEditor(model=gemini_model).create_edit_plan(transcript)
    return {"transcript": transcript, "edit_plan": edit_plan}

"""Unified local and hosted transcription providers for the web editor."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

import requests

from app.services.local_video_transcriber import transcribe_video


class TranscriptionError(RuntimeError):
    """Raised when no configured transcription provider can process media."""


def transcribe_media(
    media_path: str | Path,
    *,
    provider: str | None = None,
    language: str | None = "ko",
    model_size: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
) -> dict[str, Any]:
    """Return the editor's standard timestamped transcript format.

    ``local`` uses faster-whisper and remains the default.  ``groq`` uses the
    hosted Whisper-compatible endpoint, which is useful where the local model
    or its runtime is unavailable.  The provider may also be selected with
    ``TRANSCRIPTION_PROVIDER``.
    """

    selected = (provider or os.getenv("TRANSCRIPTION_PROVIDER", "local")).strip().lower()
    path = Path(media_path).expanduser().resolve()
    if not path.is_file():
        raise TranscriptionError(f"미디어 파일을 찾을 수 없습니다: {path}")

    if selected == "local":
        try:
            return transcribe_video(
                path,
                model_size=model_size,
                language=language,
                device=device,
                compute_type=compute_type,
            )
        except Exception as exc:
            raise TranscriptionError(f"로컬 STT에 실패했습니다: {exc}") from exc
    if selected == "groq":
        return _transcribe_with_groq(path, language=language)
    raise TranscriptionError("TRANSCRIPTION_PROVIDER는 local 또는 groq여야 합니다.")


def _transcribe_with_groq(path: Path, *, language: str | None) -> dict[str, Any]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise TranscriptionError("Groq STT를 사용하려면 GROQ_API_KEY가 필요합니다.")

    data = {
        "model": os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    if language:
        data["language"] = language
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        with path.open("rb") as media_file:
            response = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files={"file": (path.name, media_file, content_type)},
                timeout=300,
            )
        response.raise_for_status()
        payload = response.json()
    except (OSError, requests.RequestException, ValueError) as exc:
        raise TranscriptionError(f"Groq STT에 실패했습니다: {exc}") from exc

    segments = []
    for raw in payload.get("segments") or []:
        try:
            start, end = float(raw["start"]), float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(raw.get("text", "")).strip()
        if text and end > start:
            segments.append({"start": round(start, 3), "end": round(end, 3), "text": text, "words": []})
    return {
        "source_path": str(path),
        "language": payload.get("language") or language,
        "duration": round(float(payload.get("duration") or 0.0), 3),
        "segments": segments,
    }

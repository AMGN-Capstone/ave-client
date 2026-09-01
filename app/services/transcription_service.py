"""Local transcription provider for the web editor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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

    Only the local faster-whisper provider is supported.
    """

    selected = (provider or "local").strip().lower()
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
    raise TranscriptionError("지원하지 않는 STT 공급자입니다: local만 사용할 수 있습니다.")

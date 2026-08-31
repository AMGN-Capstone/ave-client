"""Local speech-to-text transcription for non-YouTube videos."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def transcribe_video(
    video_path: str | Path,
    *,
    model_size: str | None = None,
    language: str | None = "ko",
    device: str | None = None,
    compute_type: str | None = None,
) -> dict[str, Any]:
    """Transcribe a local video and return timestamped segments.

    faster-whisper can decode the audio track from common video containers
    directly. No YouTube URL or cloud STT API is required.
    """

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. Run: pip install -r requirements.txt"
        ) from exc

    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video file was not found: {path}")

    selected_device = device or os.getenv("WHISPER_DEVICE", "cpu")
    selected_model = model_size or os.getenv("WHISPER_MODEL", "small")
    selected_compute_type = compute_type or os.getenv(
        "WHISPER_COMPUTE_TYPE",
        "float16" if selected_device == "cuda" else "int8",
    )

    model = WhisperModel(
        selected_model,
        device=selected_device,
        compute_type=selected_compute_type,
    )
    segments, info = model.transcribe(
        str(path),
        language=language,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
    )

    transcript_segments: list[dict[str, Any]] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue

        words = []
        for word in segment.words or []:
            words.append(
                {
                    "start": round(float(word.start), 3),
                    "end": round(float(word.end), 3),
                    "text": word.word.strip(),
                }
            )

        transcript_segments.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": text,
                "words": words,
            }
        )

    return {
        "source_path": str(path),
        "language": getattr(info, "language", language),
        "duration": round(float(getattr(info, "duration", 0.0)), 3),
        "segments": transcript_segments,
    }


def transcript_as_text(transcript: dict[str, Any]) -> str:
    """Convert timestamped segments into a prompt-friendly text format."""

    lines = []
    for segment in transcript.get("segments", []):
        lines.append(
            f"[{segment['start']:.3f} - {segment['end']:.3f}] {segment['text']}"
        )
    return "\n".join(lines)

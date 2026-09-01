"""RunPod Queue endpoint client for ave-whisper-api."""

from __future__ import annotations

import time
from typing import Any

import requests

from app.config import (
    get_runpod_api_key,
    get_whisper_runpod_endpoint_id,
    get_whisper_runpod_timeout_seconds,
)


class WhisperAPIError(RuntimeError):
    """Raised when the configured ave-whisper-api endpoint cannot complete."""


def transcribe_with_whisper_api(audio_url: str, *, language: str = "ko", initial_prompt: str | None = None, hotwords: str | None = None, speed: float = 1.0) -> dict[str, Any]:
    endpoint_id = get_whisper_runpod_endpoint_id()
    api_key = get_runpod_api_key()
    if not endpoint_id or not api_key:
        raise WhisperAPIError("Whisper API를 사용하려면 WHISPER_RUNPOD_ENDPOINT_ID와 RUNPOD_API_KEY를 설정하세요.")
    endpoint = f"https://api.runpod.ai/v2/{endpoint_id}"
    payload = {"input": {"audio_url": audio_url, "language": language, "initial_prompt": initial_prompt, "hotwords": hotwords, "speed": speed}}
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = requests.post(f"{endpoint}/run", json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        job_id = response.json().get("id")
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise WhisperAPIError("Whisper API 작업 요청에 실패했습니다.") from exc
    if not isinstance(job_id, str) or not job_id:
        raise WhisperAPIError("Whisper API가 작업 ID를 반환하지 않았습니다.")
    deadline = time.monotonic() + get_whisper_runpod_timeout_seconds()
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{endpoint}/status/{job_id}", headers=headers, timeout=30)
            response.raise_for_status()
            status = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise WhisperAPIError("Whisper API 작업 상태를 조회하지 못했습니다.") from exc
        state = str(status.get("status", "")).upper()
        if state == "COMPLETED":
            output = status.get("output")
            if not isinstance(output, dict):
                raise WhisperAPIError("Whisper API 응답 형식이 올바르지 않습니다.")
            error = output.get("error")
            if isinstance(error, dict):
                raise WhisperAPIError(str(error.get("message") or "Whisper API 전사에 실패했습니다."))
            if not isinstance(output.get("segments"), list):
                raise WhisperAPIError("Whisper API 응답에 전사 구간이 없습니다.")
            return output
        if state in {"FAILED", "CANCELLED", "TIMED_OUT"}:
            raise WhisperAPIError(str(status.get("error") or "Whisper API 전사에 실패했습니다."))
        time.sleep(2)
    raise WhisperAPIError("Whisper API 전사 작업 시간이 초과되었습니다.")

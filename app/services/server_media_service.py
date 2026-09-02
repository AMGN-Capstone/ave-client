"""AVE 서버 HTTP API를 통한 임시 오디오 전사 요청."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests

from app.config import get_ave_server_url
from app.services.toolchain import ToolchainError, ffmpeg as get_ffmpeg


class ServerMediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerMediaFile:
    file_id: str
    public_url: str


def upload_audio_for_transcription(source: str | Path, access_token: str) -> ServerMediaFile:
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise ServerMediaError(f"원본 영상 파일을 찾을 수 없습니다: {source_path}")
    endpoint = _server_url()
    if not access_token:
        raise ServerMediaError("원격 Whisper 전사를 위해 로그인 토큰이 필요합니다.")
    with tempfile.TemporaryDirectory(prefix="ave-whisper-audio-") as directory:
        audio_path = Path(directory) / "audio.mp3"
        _extract_audio(source_path, audio_path)
        try:
            with audio_path.open("rb") as audio:
                response = requests.post(
                    f"{endpoint}/api/stt-files",
                    headers={"Authorization": access_token},
                    files={"file": ("audio.mp3", audio, "audio/mpeg")},
                    timeout=600,
                )
            response.raise_for_status()
            payload = response.json()
        except (OSError, requests.RequestException, ValueError) as exc:
            raise ServerMediaError("AVE 서버에 전사용 오디오를 업로드하지 못했습니다.") from exc
    file_id = payload.get("file_id")
    public_url = payload.get("public_url")
    if not isinstance(file_id, str) or not isinstance(public_url, str):
        raise ServerMediaError("AVE 서버의 임시 오디오 응답이 올바르지 않습니다.")
    return ServerMediaFile(file_id=file_id, public_url=public_url)


def transcribe_uploaded_audio(file_id: str, access_token: str, *, language: str, initial_prompt: str | None, hotwords: str | None, speed: float) -> dict:
    try:
        response = requests.post(
            f"{_server_url()}/api/stt/transcriptions",
            headers={"Authorization": access_token, "Content-Type": "application/json"},
            json={"file_id": file_id, "language": language, "initial_prompt": initial_prompt, "hotwords": hotwords, "speed": speed},
            timeout=3700,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ServerMediaError("AVE 서버에 Whisper 전사를 요청하지 못했습니다.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ServerMediaError("AVE 서버의 Whisper 전사 응답이 올바르지 않습니다.")
    return payload


def _server_url() -> str:
    value = get_ave_server_url()
    if not value.startswith("https://"):
        raise ServerMediaError("AVE_SERVER_URL에 AVE 서버의 HTTPS 주소를 설정하세요.")
    return value


def _extract_audio(source: Path, output: Path) -> None:
    try:
        ffmpeg = str(get_ffmpeg())
    except ToolchainError as exc:
        raise ServerMediaError(str(exc)) from exc
    try:
        completed = subprocess.run([ffmpeg, "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-q:a", "4", str(output)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise ServerMediaError("ffmpeg로 음원을 추출하지 못했습니다.") from exc
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise ServerMediaError(completed.stderr[-1000:] or "ffmpeg 음원 추출에 실패했습니다.")

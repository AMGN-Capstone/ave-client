"""Temporary Azure SFTP media hosting for remote Whisper transcription."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from app.config import (
    get_azure_media_known_hosts_path,
    get_azure_media_public_base_url,
    get_azure_media_sftp_host,
    get_azure_media_sftp_key_path,
    get_azure_media_sftp_port,
    get_azure_media_sftp_user,
)


class AzureMediaError(RuntimeError):
    """Raised when temporary media cannot be published to the Azure VM."""


@dataclass(frozen=True)
class AzureMediaFile:
    remote_name: str
    public_url: str


def upload_audio_for_transcription(source: str | Path, job_id: str) -> AzureMediaFile:
    """Extract MP3 audio, upload it through verified SFTP, and return its HTTPS URL."""

    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise AzureMediaError(f"원본 영상 파일을 찾을 수 없습니다: {source_path}")
    _validate_job_id(job_id)
    remote_name = f"{job_id}-{uuid4().hex}.mp3"

    with tempfile.TemporaryDirectory(prefix="ave-whisper-audio-") as directory:
        audio_path = Path(directory) / remote_name
        _extract_audio(source_path, audio_path)
        client, sftp = _open_sftp()
        try:
            sftp.put(str(audio_path), remote_name)
        except Exception as exc:
            raise AzureMediaError("Azure 전사용 음원 업로드에 실패했습니다.") from exc
        finally:
            sftp.close()
            client.close()

    return AzureMediaFile(remote_name=remote_name, public_url=f"{_public_base_url()}/files/{quote(remote_name)}")


def delete_uploaded_audio(remote_name: str) -> None:
    """Best-effort cleanup after RunPod has finished reading the file."""

    if Path(remote_name).name != remote_name:
        raise AzureMediaError("삭제할 원격 파일 이름이 올바르지 않습니다.")
    client, sftp = _open_sftp()
    try:
        sftp.remove(remote_name)
    except FileNotFoundError:
        return
    except Exception as exc:
        raise AzureMediaError("Azure 임시 음원 삭제에 실패했습니다.") from exc
    finally:
        sftp.close()
        client.close()


def _extract_audio(source: Path, output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AzureMediaError("전사용 음원을 만들려면 ffmpeg가 필요합니다.")
    try:
        completed = subprocess.run(
            [ffmpeg, "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-q:a", "4", str(output)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise AzureMediaError("ffmpeg로 음원을 추출하지 못했습니다.") from exc
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise AzureMediaError(completed.stderr[-1000:] or "ffmpeg 음원 추출에 실패했습니다.")


def _open_sftp():
    try:
        import paramiko
    except ImportError as exc:  # pragma: no cover - guarded by requirements
        raise AzureMediaError("SFTP 업로드를 위해 paramiko를 설치하세요.") from exc

    host = get_azure_media_sftp_host()
    key_path = Path(get_azure_media_sftp_key_path()).expanduser()
    known_hosts = Path(get_azure_media_known_hosts_path()).expanduser()
    if not host or not get_azure_media_sftp_user() or not key_path.is_file() or not known_hosts.is_file():
        raise AzureMediaError(
            "Azure SFTP 설정이 없습니다. 호스트, 개인키 및 known_hosts 경로를 token.env에 설정하세요."
        )
    client = paramiko.SSHClient()
    client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(
            hostname=host,
            port=get_azure_media_sftp_port(),
            username=get_azure_media_sftp_user(),
            key_filename=str(key_path),
            look_for_keys=False,
            allow_agent=False,
            timeout=30,
        )
        return client, client.open_sftp()
    except Exception as exc:
        client.close()
        raise AzureMediaError("Azure SFTP 서버에 연결하지 못했습니다.") from exc


def _public_base_url() -> str:
    value = get_azure_media_public_base_url()
    if not value.startswith("https://"):
        raise AzureMediaError("AZURE_MEDIA_PUBLIC_BASE_URL은 공개 HTTPS 주소여야 합니다.")
    return value


def _validate_job_id(job_id: str) -> None:
    if not job_id or Path(job_id).name != job_id:
        raise AzureMediaError("작업 ID가 올바르지 않습니다.")

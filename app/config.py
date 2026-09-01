import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / "token.env")


def get_media_root() -> Path:
    return Path(os.getenv("MEDIA_ROOT", "media")).resolve()


def get_supabase_url() -> str:
    return os.getenv("SUPABASE_URL", "").strip()


def get_supabase_anon_key() -> str:
    return os.getenv("SUPABASE_ANON_KEY", "").strip()


def get_supabase_service_role_key() -> str:
    return os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()


def get_whisper_runpod_endpoint_id() -> str:
    return os.getenv("WHISPER_RUNPOD_ENDPOINT_ID", "").strip()


def get_runpod_api_key() -> str:
    return os.getenv("RUNPOD_API_KEY", "").strip()


def get_whisper_runpod_timeout_seconds() -> int:
    """Maximum time to wait for a RunPod Queue transcription job."""
    value = os.getenv("WHISPER_RUNPOD_TIMEOUT_SECONDS", "3600").strip()
    try:
        timeout = int(value)
    except ValueError as exc:
        raise ValueError("WHISPER_RUNPOD_TIMEOUT_SECONDS must be a positive integer.") from exc
    if timeout <= 0:
        raise ValueError("WHISPER_RUNPOD_TIMEOUT_SECONDS must be a positive integer.")
    return timeout


def get_azure_media_sftp_host() -> str:
    return os.getenv("AZURE_MEDIA_SFTP_HOST", "").strip()


def get_azure_media_sftp_port() -> int:
    return int(os.getenv("AZURE_MEDIA_SFTP_PORT", "22"))


def get_azure_media_sftp_user() -> str:
    return os.getenv("AZURE_MEDIA_SFTP_USER", "ave-media").strip()


def get_azure_media_sftp_key_path() -> str:
    return os.getenv("AZURE_MEDIA_SFTP_KEY_PATH", "").strip()


def get_azure_media_known_hosts_path() -> str:
    return os.getenv("AZURE_MEDIA_SSH_KNOWN_HOSTS", "").strip()


def get_azure_media_public_base_url() -> str:
    return os.getenv("AZURE_MEDIA_PUBLIC_BASE_URL", "").strip().rstrip("/")

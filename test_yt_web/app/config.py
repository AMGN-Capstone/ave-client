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

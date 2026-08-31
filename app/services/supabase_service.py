from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import (
    get_supabase_anon_key,
    get_supabase_service_role_key,
    get_supabase_url,
)

try:
    from supabase import Client, create_client
except ImportError:  # pragma: no cover - dependency is required in production
    Client = Any
    create_client = None


BUCKET_NAME = "longform-media"


def is_configured() -> bool:
    return bool(get_supabase_url() and get_supabase_anon_key())


def is_server_configured() -> bool:
    return bool(get_supabase_url() and get_supabase_service_role_key())


@lru_cache(maxsize=1)
def get_auth_client() -> Client:
    if create_client is None:
        raise RuntimeError("supabase package is not installed.")
    if not is_configured():
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY are required.")
    return create_client(get_supabase_url(), get_supabase_anon_key())


@lru_cache(maxsize=1)
def get_server_client() -> Client:
    if create_client is None:
        raise RuntimeError("supabase package is not installed.")
    if not is_server_configured():
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required on the server."
        )
    return create_client(get_supabase_url(), get_supabase_service_role_key())


def upload_file(storage_path: str, local_path: Path, content_type: str) -> None:
    client = get_server_client()
    with local_path.open("rb") as file_obj:
        client.storage.from_(BUCKET_NAME).upload(
            storage_path,
            file_obj,
            {"content-type": content_type, "upsert": "true"},
        )


def insert_row(table: str, values: dict[str, Any]) -> dict[str, Any]:
    response = get_server_client().table(table).insert(values).execute()
    rows = response.data or []
    if not rows:
        raise RuntimeError(f"Supabase did not return the inserted {table} row.")
    return rows[0]


def update_row(table: str, row_id: str, values: dict[str, Any]) -> None:
    get_server_client().table(table).update(values).eq("id", row_id).execute()

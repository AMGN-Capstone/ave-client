"""SQLite persistence for local edit job state and analysis results."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LocalJobStoreError(RuntimeError):
    pass


class LocalJobStore:
    """Store processing data separately from yt-dlp and rendered media files."""

    def __init__(self, database_root: Path):
        self.path = database_root / "ave-client.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS edit_jobs (
                    job_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    plan_json TEXT,
                    raw_transcript_json TEXT,
                    cleaned_transcript_json TEXT,
                    summary_json TEXT,
                    candidates_json TEXT,
                    revisions_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_cache (
                    cache_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load(value: str | None, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_or_update_state(self, job_id: str, state: dict[str, Any]) -> None:
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM edit_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            merged = self._load(row["state_json"], {}) if row else {}
            merged.update(state)
            connection.execute(
                """
                INSERT INTO edit_jobs (job_id, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (job_id, self._dump(merged), now, now),
            )

    def get_state(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM edit_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._load(row["state_json"], {}) if row else None

    def save_analysis(
        self,
        job_id: str,
        *,
        plan: dict[str, Any],
        raw_transcript: dict[str, Any],
        cleaned_transcript: dict[str, Any],
        summary: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO edit_jobs (
                    job_id, plan_json, raw_transcript_json, cleaned_transcript_json,
                    summary_json, candidates_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    plan_json = excluded.plan_json,
                    raw_transcript_json = excluded.raw_transcript_json,
                    cleaned_transcript_json = excluded.cleaned_transcript_json,
                    summary_json = excluded.summary_json,
                    candidates_json = excluded.candidates_json,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    self._dump(plan),
                    self._dump(raw_transcript),
                    self._dump(cleaned_transcript),
                    self._dump(summary),
                    self._dump(candidates),
                    now,
                    now,
                ),
            )

    def get_analysis(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM edit_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None or row["plan_json"] is None:
            return None
        return {
            "plan": self._load(row["plan_json"], {}),
            "raw_transcript": self._load(row["raw_transcript_json"], {}),
            "cleaned_transcript": self._load(row["cleaned_transcript_json"], {}),
            "summary": self._load(row["summary_json"], {}),
            "candidates": self._load(row["candidates_json"], []),
            "revisions": self._load(row["revisions_json"], []),
        }

    def update_plan(self, job_id: str, plan: dict[str, Any]) -> None:
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE edit_jobs SET plan_json = ?, updated_at = ? WHERE job_id = ?",
                (self._dump(plan), now, job_id),
            )
        if cursor.rowcount != 1:
            raise LocalJobStoreError("저장할 로컬 편집 작업을 찾을 수 없습니다.")

    def append_revision(self, job_id: str, revision: dict[str, Any]) -> None:
        analysis = self.get_analysis(job_id)
        if analysis is None:
            raise LocalJobStoreError("저장할 로컬 편집 작업을 찾을 수 없습니다.")
        history = analysis["revisions"] if isinstance(analysis["revisions"], list) else []
        history.append(revision)
        with self._connect() as connection:
            connection.execute(
                "UPDATE edit_jobs SET revisions_json = ?, updated_at = ? WHERE job_id = ?",
                (self._dump(history[-100:]), self._now(), job_id),
            )

    def get_llm_cache(self, cache_key: str) -> Any | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM llm_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
        return self._load(row["value_json"], None) if row else None

    def save_llm_cache(self, cache_key: str, value: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO llm_cache (cache_key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (cache_key, self._dump(value), self._now()),
            )

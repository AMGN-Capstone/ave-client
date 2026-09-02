"""AVE 서버에 로컬 편집 작업 이력과 분석 결과를 기록한다."""

from __future__ import annotations

from typing import Any

import requests

from app.config import get_ave_server_url


class ServerJobError(RuntimeError):
    pass


def create_job(access_token: str, *, client_job_id: str, source_id: str, source_url: str) -> str:
    payload = _request("POST", "/api/analysis-jobs", access_token, {"client_job_id": client_job_id, "source_id": source_id, "source_url": source_url})
    job_id = payload.get("id")
    if not isinstance(job_id, str):
        raise ServerJobError("AVE 서버가 작업 ID를 반환하지 않았습니다.")
    return job_id


def update_job(access_token: str, job_id: str, *, status: str, progress: int, error_message: str | None = None) -> None:
    _request("PATCH", f"/api/analysis-jobs/{job_id}", access_token, {"status": status, "progress": progress, "error_message": error_message})


def save_result(access_token: str, job_id: str, result: dict[str, Any], *, selection: dict[str, Any] | None = None) -> None:
    candidates = result.get("candidates") or []
    segments = [
        {
            "segment_index": index,
            "start_ms": int(float(item.get("start", 0)) * 1000),
            "end_ms": max(1, int(float(item.get("end", 0)) * 1000)),
            "text": str(item.get("text", "")),
            "script_importance": _score(item.get("llm_score")),
            "chat_density": _number(item.get("chat_density")),
            "comment_timestamp_count": _integer(item.get("chat_count")),
            "heatmap_score": None,
            "average_volume_dbfs": _number(item.get("average_volume_dbfs")),
            "final_score": _score(item.get("final_score")),
            "recommended": str(item.get("segment_id")) in set(result.get("recommended_segment_ids") or []),
        }
        for index, item in enumerate(candidates)
        if float(item.get("end", 0)) > float(item.get("start", 0))
    ]
    _request("PUT", f"/api/analysis-jobs/{job_id}/result", access_token, {"script": None, "segments": segments, "heatmap": [], "recommendation": {"summary": result.get("summary"), "recommended_segment_ids": result.get("recommended_segment_ids", [])}, "selection": selection or {}})


def _request(method: str, path: str, access_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    base_url = get_ave_server_url()
    if not base_url.startswith("https://") or not access_token:
        raise ServerJobError("AVE_SERVER_URL과 로그인 토큰이 필요합니다.")
    try:
        response = requests.request(method, f"{base_url}{path}", headers={"Authorization": access_token, "Content-Type": "application/json"}, json=payload, timeout=30)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ServerJobError("AVE 서버와 작업 이력을 동기화하지 못했습니다.") from exc
    return body if isinstance(body, dict) else {}


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value: object) -> int | None:
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _score(value: object) -> float | None:
    number = _number(value)
    return min(1.0, max(0.0, number)) if number is not None else None

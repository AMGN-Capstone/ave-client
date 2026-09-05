import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.main import LIVE_EDIT_JOBS, METADATA_MATERIAL_JOBS, app, get_youtube_metadata_material_job, index, start_live_edit, update_edit_segments
from app.schemas import LiveEditRequest, SegmentSelectionRequest


def test_frontend_index_is_served():
    response = asyncio.run(index())

    assert response.status_code == 200
    assert response.path.name == "index.html"


def test_workflow_uses_current_endpoints_and_restored_options():
    source = (Path(__file__).resolve().parents[1] / "ui" / "src" / "WorkflowApp.tsx").read_text(encoding="utf-8")

    for value in (
        "/api/youtube/metadata",
        "/api/youtube/edit/start",
        "transcript_language",
        "stt_language",
        "stt_initial_prompt",
        "stt_hotwords",
        "stt_speed",
        "subtitle_font_name",
        "subtitle_font_size",
    ):
        assert value in source
    assert "interactive_selection" not in source
    assert "/subtitles" not in source
    assert "subtitle_offset_seconds" not in source
    assert "scriptSourceOptions" in source
    options_start = source.index("const scriptSourceOptions")
    options_end = source.index("function transitionToPhase", options_start)
    options = source[options_start:options_end]
    assert options.index("youtube_subtitle") < options.index("youtube_caption") < options.index("whisper_api")
    assert "review.segments" not in source
    assert "section.final_score" not in source
    assert 'className="score-badge"' in source
    assert "Math.round((Number(body.duration_seconds) || 0) / 4)" in source
    assert "function transitionToPhase" in source
    assert "runTransition" not in source
    assert "function DetailedTime" in source
    assert "formatMilliseconds" in source
    assert "addEventListener('seeked'" in source
    assert 'className="header-separator"' in source


def test_removed_legacy_routes_are_not_registered():
    paths = {route.path for route in app.routes}

    assert "/api/videos/upload" not in paths
    assert "/api/youtube/live/inspect" not in paths
    assert "/api/youtube/edit" not in paths


def test_metadata_material_terminal_status_is_consumed_once():
    job_id = "metadata-terminal"
    METADATA_MATERIAL_JOBS[job_id] = {"job_id": job_id, "status": "completed", "result": {"video_id": "dQw4w9WgXcQ"}}
    response = asyncio.run(get_youtube_metadata_material_job(job_id))

    assert response["status"] == "completed"
    assert job_id not in METADATA_MATERIAL_JOBS
    with pytest.raises(HTTPException, match="찾을 수 없습니다"):
        asyncio.run(get_youtube_metadata_material_job(job_id))


def test_edit_request_defaults_match_workflow_defaults():
    request = LiveEditRequest(vod_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert request.llm_provider == "deepseek"
    assert request.transcription_source == "youtube_caption"
    assert request.transcript_language is None


def test_selection_api_starts_render_with_only_segment_ids(monkeypatch):
    job_id = "selection-contract"
    LIVE_EDIT_JOBS[job_id] = {
        "job_id": job_id,
        "status": "awaiting_selection",
        "phase": "selection",
        "result": {"analysis_plan": {"candidates": [{"segment_id": "chapter-00", "start": 0, "end": 10}]}},
    }
    created = []

    def no_background_task(coroutine):
        created.append(coroutine)
        coroutine.close()

    monkeypatch.setattr("app.main.asyncio.create_task", no_background_task)
    response = asyncio.run(
        update_edit_segments(job_id, SegmentSelectionRequest(segment_ids=["chapter-00"]))
    )

    assert response["phase"] == "render"
    assert len(created) == 1
    LIVE_EDIT_JOBS.pop(job_id, None)


def test_analysis_start_api_queues_only_memory_job(monkeypatch):
    created = []

    def no_background_task(coroutine):
        created.append(coroutine)
        coroutine.close()

    monkeypatch.setattr("app.main.asyncio.create_task", no_background_task)
    response = asyncio.run(start_live_edit(LiveEditRequest(vod_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"), authorization="Bearer session"))

    assert response["status"] == "queued"
    assert response["job_id"] in LIVE_EDIT_JOBS
    assert len(created) == 1
    LIVE_EDIT_JOBS.pop(response["job_id"], None)

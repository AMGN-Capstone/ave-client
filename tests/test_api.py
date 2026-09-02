import json
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import (
    LIVE_EDIT_JOBS,
    _run_live_edit_job,
    app,
    get_current_user,
)
from app.schemas import LiveEditRequest
from app.services.live_youtube_service import LiveYouTubeError
from app.services.local_job_store import LocalJobStore

import asyncio


def test_frontend_index_is_served():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert '/ui/assets/' in response.text
    return
    assert "업로드 완료 영상 자동 편집" in response.text
    assert "채팅 리플레이가 지원되면" in response.text
    assert "Google 로그인" in response.text
    assert "구간 검토 및 영상 생성" in response.text
    assert "챕터 선택" in response.text
    assert "세부 구간을 바로 펼쳐 조정" in response.text
    assert 'id="chapterList"' in response.text
    assert 'id="analysisPhase"' in response.text
    assert 'id="renderPhase"' in response.text
    assert 'name="chat_delay_seconds"' in response.text
    assert "AI로 자막 오타·중복 정제" in response.text
    assert 'id="segmentList"' not in response.text


def test_youtube_metadata_returns_preview_contract(monkeypatch):
    monkeypatch.setattr(
        "app.main.get_video_metadata",
        lambda _url: {
            "title": "테스트 영상",
            "video_id": "dQw4w9WgXcQ",
            "thumbnail_files": [{"url": "/api/youtube/thumbnail/dQw4w9WgXcQ/sddefault.jpg", "is_primary": True}],
            "chapters": [{"start_time": 0, "end_time": 60, "title": "시작"}],
            "heatmap": [{"start_time": 0, "end_time": 10, "value": 0.8}],
        },
    )
    client = TestClient(app)

    response = client.post("/api/youtube/metadata", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})

    assert response.status_code == 200
    assert response.json()["chapters"][0]["title"] == "시작"
    assert response.json()["heatmap"][0]["value"] == 0.8


def test_upload_video_saves_supported_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/videos/upload",
        files={"file": ("lesson.mp4", BytesIO(b"fake video bytes"), "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["original_filename"] == "lesson.mp4"
    assert body["stored_filename"].endswith(".mp4")
    assert body["content_type"] == "video/mp4"
    assert (tmp_path / "yt-edit" / "uploads" / Path(body["stored_filename"]).stem / body["stored_filename"]).exists()


def test_upload_video_rejects_unsupported_extension(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/videos/upload",
        files={"file": ("notes.txt", BytesIO(b"not a video"), "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported video format."


def test_completed_video_edit_continues_when_chat_replay_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))

    class FakePipeline:
        def __init__(self, _media_root, *, database_root=None):
            pass

        def run(self, **kwargs):
            assert kwargs["archive_path"].name == "chat-replay.jsonl"
            return {"awaiting_selection": True, "summary": {"summary": "내용 요약"}}

    def unavailable_replay(*_args, **_kwargs):
        raise LiveYouTubeError("채팅 리플레이가 없습니다.")

    monkeypatch.setattr("app.main.LiveEditPipeline", FakePipeline)
    monkeypatch.setattr("app.main.collect_chat_replay", unavailable_replay)
    monkeypatch.setattr("app.main.create_server_job", lambda *_args, **_kwargs: "server-job")
    monkeypatch.setattr("app.main.update_server_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.main.save_server_result", lambda *_args, **_kwargs: None)
    job_id = "completed-video-no-chat"
    LIVE_EDIT_JOBS[job_id] = {"job_id": job_id, "status": "queued"}

    asyncio.run(
        _run_live_edit_job(
            job_id,
                LiveEditRequest(vod_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
                "Bearer test-token",
        )
    )

    assert LIVE_EDIT_JOBS[job_id]["status"] == "awaiting_selection"
    assert LIVE_EDIT_JOBS[job_id]["result"]["chat_replay_used"] is False


def test_segment_review_and_source_preview_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    job_id = "segment-api-job"
    output_dir = tmp_path / "yt-edit" / job_id
    output_dir.mkdir(parents=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake mp4")
    (output_dir / "edit_plan.json").write_text(
        json.dumps(
            {
                "genre": "game",
                "target_seconds": 60,
                "source_video_path": str(source),
                "render_mode": "preview",
                "candidates": [
                    {
                        "segment_id": "segment-0000",
                        "start": 5.0,
                        "end": 15.0,
                        "text": "결정적 장면",
                        "final_score": 910,
                    }
                ],
                "recommended_segment_ids": ["segment-0000"],
                "selected_segment_ids": ["segment-0000"],
                "clips": [{"start": 4.6, "end": 15.6}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    client = TestClient(app)

    plan = json.loads((output_dir / "edit_plan.json").read_text(encoding="utf-8"))
    LocalJobStore(tmp_path / "db").save_analysis(
        job_id,
        plan=plan,
        raw_transcript={"segments": []},
        cleaned_transcript={"segments": []},
        summary={},
        candidates=plan["candidates"],
    )
    (output_dir / "edit_plan.json").unlink()
    review_response = client.get(f"/api/youtube/edit/{job_id}/segments")
    media_response = client.get(f"/api/youtube/edit/{job_id}/media/source")

    assert review_response.status_code == 200
    review = review_response.json()
    assert review["segments"][0]["selected"] is True
    assert review["segments"][0]["chapter_id"] == "chapter-00"
    assert review["chapters"][0]["segment_ids"] == ["segment-0000"]
    assert media_response.status_code == 200
    assert media_response.content == b"fake mp4"

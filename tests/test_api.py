import json
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import (
    LIVE_EDIT_JOBS,
    _run_live_edit_job,
    app,
    get_current_user,
    get_youtube_importer,
)
from app.schemas import LiveEditRequest
from app.services.live_youtube_service import LiveYouTubeError

import asyncio


class FakeYouTubeImporter:
    async def import_video(self, url: str, job_id: str | None = None):
        return {
            "job_id": job_id or "sample-job",
            "source_url": url,
            "title": "Sample information video",
            "duration": 1234,
            "video_path": "media/yt-data/sample-video-id/video.mp4",
            "subtitle_files": ["media/yt-data/sample-video-id/subtitles.ko.vtt"],
            "metadata_path": "media/yt-data/sample-video-id/sample-video-id.info.json",
            "warnings": [],
        }


def test_youtube_import_returns_collected_asset_summary(monkeypatch):
    monkeypatch.setattr("app.main.is_server_configured", lambda: True)
    monkeypatch.setattr("app.main.insert_row", lambda _table, values: values)
    monkeypatch.setattr("app.main.update_row", lambda _table, _row_id, _values: None)
    monkeypatch.setattr("app.main.upload_file", lambda *_args: None)
    app.dependency_overrides[get_youtube_importer] = lambda: FakeYouTubeImporter()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "test@example.com",
    }
    client = TestClient(app)

    response = client.post(
        "/api/youtube/import",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "job_id": response.json()["job_id"],
        "video_id": response.json()["video_id"],
        "transcript_id": None,
        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "Sample information video",
        "duration": 1234,
        "video_path": None,
        "subtitle_files": [],
        "metadata_path": "",
        "warnings": [],
    }


def test_frontend_index_is_served():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "업로드 완료 영상 자동 편집" in response.text
    assert "채팅 리플레이가 지원되면" in response.text
    assert "Google 로그인" in response.text
    assert "구간 검토 및 영상 생성" in response.text
    assert "선택 구간으로 영상 생성" in response.text
    assert "챕터 선택" in response.text
    assert "세부 구간을 바로 펼쳐 조정" in response.text
    assert 'id="chapterList"' in response.text
    assert 'id="analysisPhase"' in response.text
    assert 'id="renderPhase"' in response.text
    assert 'name="chat_delay_seconds"' in response.text
    assert "AI로 자막 오타·중복 정제" in response.text
    assert 'id="segmentList"' not in response.text


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
        def __init__(self, _media_root):
            pass

        def run(self, **kwargs):
            assert kwargs["archive_path"].name == "vod-dQw4w9WgXcQ.jsonl"
            return {"awaiting_selection": True, "summary": {"summary": "내용 요약"}}

    def unavailable_replay(*_args, **_kwargs):
        raise LiveYouTubeError("채팅 리플레이가 없습니다.")

    monkeypatch.setattr("app.main.LiveEditPipeline", FakePipeline)
    monkeypatch.setattr("app.main.collect_chat_replay", unavailable_replay)
    job_id = "completed-video-no-chat"
    LIVE_EDIT_JOBS[job_id] = {"job_id": job_id, "status": "queued"}

    asyncio.run(
        _run_live_edit_job(
            job_id,
            LiveEditRequest(vod_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
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

    review_response = client.get(f"/api/youtube/edit/{job_id}/segments")
    media_response = client.get(f"/api/youtube/edit/{job_id}/media/source")

    assert review_response.status_code == 200
    review = review_response.json()
    assert review["segments"][0]["selected"] is True
    assert review["segments"][0]["chapter_id"] == "chapter-00"
    assert review["chapters"][0]["segment_ids"] == ["segment-0000"]
    assert media_response.status_code == 200
    assert media_response.content == b"fake mp4"

from io import BytesIO

from fastapi.testclient import TestClient

from app.main import app, get_current_user, get_youtube_importer


class FakeYouTubeImporter:
    async def import_video(self, url: str, job_id: str | None = None):
        return {
            "job_id": job_id or "sample-job",
            "source_url": url,
            "title": "Sample information video",
            "duration": 1234,
            "video_path": "media/youtube/sample-job/video.mp4",
            "subtitle_files": ["media/youtube/sample-job/subtitles.ko.vtt"],
            "metadata_path": "media/youtube/sample-job/metadata.json",
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
    assert "라이브 채팅 기반 자동 편집" in response.text
    assert "다시보기와 분석 결합" in response.text
    assert "Google 로그인" in response.text


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
    assert (tmp_path / "uploads" / body["stored_filename"]).exists()


def test_upload_video_rejects_unsupported_extension(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/videos/upload",
        files={"file": ("notes.txt", BytesIO(b"not a video"), "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported video format."

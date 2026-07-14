from io import BytesIO

from fastapi.testclient import TestClient

from app.main import app, get_youtube_importer


class FakeYouTubeImporter:
    async def import_video(self, url: str):
        return {
            "job_id": "sample-job",
            "source_url": url,
            "title": "Sample information video",
            "duration": 1234,
            "video_path": "media/youtube/sample-job/video.mp4",
            "subtitle_files": ["media/youtube/sample-job/subtitles.ko.vtt"],
            "metadata_path": "media/youtube/sample-job/metadata.json",
            "warnings": [],
        }


def test_youtube_import_returns_collected_asset_summary():
    app.dependency_overrides[get_youtube_importer] = lambda: FakeYouTubeImporter()
    client = TestClient(app)

    response = client.post(
        "/api/youtube/import",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "job_id": "sample-job",
        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "Sample information video",
        "duration": 1234,
        "video_path": "media/youtube/sample-job/video.mp4",
        "subtitle_files": ["media/youtube/sample-job/subtitles.ko.vtt"],
        "metadata_path": "media/youtube/sample-job/metadata.json",
        "warnings": [],
    }


def test_frontend_index_is_served():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "YouTube URL" in response.text
    assert "직접 영상 업로드" in response.text


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

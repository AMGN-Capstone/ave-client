import json

import pytest

from app.services.live_youtube_service import LiveYouTubeError, _download_thumbnail_list, get_video_metadata


def test_thumbnail_list_saves_every_ytdlp_thumbnail_entry(tmp_path):
    class Response:
        def read(self):
            return b"thumbnail bytes"

        def close(self):
            pass

    class Downloader:
        urls = []

        def urlopen(self, url):
            self.urls.append(url)
            return Response()

    saved = _download_thumbnail_list(
        Downloader(),
        [
            {"url": "https://i.ytimg.com/vi/video/sddefault.jpg"},
            {"url": "https://i.ytimg.com/vi/video/sd1.jpg"},
            {"url": "https://i.ytimg.com/vi/video/sd2.jpg"},
            {"url": "https://i.ytimg.com/vi/video/sd3.jpg"},
        ],
        None,
        tmp_path,
        "video-id",
    )

    assert len(saved) == 4
    assert saved[0]["is_primary"] is True
    assert (tmp_path / "thumbnails" / "sd3.jpg").read_bytes() == b"thumbnail bytes"


def test_metadata_uses_existing_info_json_without_calling_ytdlp(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    video_id = "abc123def45"
    output_dir = tmp_path / "yt-data" / video_id
    output_dir.mkdir(parents=True)
    (output_dir / f"{video_id}.info.json").write_text(
            json.dumps({"id": video_id, "title": "cached title", "thumbnail": "https://example.com/default.jpg", "duration": 600}),
        encoding="utf-8",
    )

    result = get_video_metadata(f"https://www.youtube.com/watch?v={video_id}", refresh=False)

    assert result["title"] == "cached title"
    assert result["video_id"] == video_id


@pytest.mark.parametrize("duration", [599, 21_600])
def test_metadata_rejects_videos_outside_supported_duration(tmp_path, monkeypatch, duration):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    video_id = "abc123def45"
    output_dir = tmp_path / "yt-data" / video_id
    output_dir.mkdir(parents=True)
    (output_dir / f"{video_id}.info.json").write_text(
        json.dumps({"id": video_id, "title": "cached title", "duration": duration}), encoding="utf-8"
    )

    with pytest.raises(LiveYouTubeError, match="10분 이상 6시간 미만"):
        get_video_metadata(f"https://www.youtube.com/watch?v={video_id}", refresh=False)

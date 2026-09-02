import json
import sys
from pathlib import Path
from types import SimpleNamespace

from app.services.live_youtube_service import (
    _download_thumbnail_list,
    analyze_chat_archive,
    collect_chat_replay,
    get_video_metadata,
)


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

    downloader = Downloader()
    saved = _download_thumbnail_list(
        downloader,
        [
            {"url": "https://i.ytimg.com/vi/video/sddefault.jpg", "width": 640, "height": 480},
            {"url": "https://i.ytimg.com/vi/video/sd1.jpg", "width": 640, "height": 480},
            {"url": "https://i.ytimg.com/vi/video/sd2.jpg", "width": 640, "height": 480},
            {"url": "https://i.ytimg.com/vi/video/sd3.jpg", "width": 640, "height": 480},
            {"url": "https://i.ytimg.com/vi/video/1.webp", "width": 480, "height": 360},
        ],
        "https://i.ytimg.com/vi/video/sddefault.jpg",
        tmp_path,
        "video-id",
    )

    assert downloader.urls == [
        "https://i.ytimg.com/vi/video/sddefault.jpg",
        "https://i.ytimg.com/vi/video/sd1.jpg",
        "https://i.ytimg.com/vi/video/sd2.jpg",
        "https://i.ytimg.com/vi/video/sd3.jpg",
    ]
    assert len(saved) == 4
    assert saved[0]["is_primary"] is True
    assert saved[3]["url"].endswith("/sd3.jpg")
    assert (tmp_path / "thumbnails" / "sd3.jpg").read_bytes() == b"thumbnail bytes"


def test_metadata_uses_existing_info_json_without_calling_ytdlp(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    video_id = "abc123def45"
    output_dir = tmp_path / "yt-data" / video_id
    output_dir.mkdir(parents=True)
    (output_dir / f"{video_id}.info.json").write_text(
        json.dumps({"id": video_id, "title": "cached title", "thumbnail": "https://example.com/default.jpg"}),
        encoding="utf-8",
    )

    result = get_video_metadata(f"https://www.youtube.com/watch?v={video_id}")

    assert result["title"] == "cached title"
    assert result["video_id"] == video_id


def test_ytdlp_replay_is_saved_with_elapsed_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is True
            output_dir = Path(self.options["outtmpl"]).parent
            output_dir.mkdir(parents=True, exist_ok=True)
            action = {
                "replayChatItemAction": {
                    "videoOffsetTimeMsec": "75000",
                    "actions": [{"addChatItemAction": {"item": {
                        "liveChatTextMessageRenderer": {"id": "replay-1", "message": {"simpleText": "peak"}},
                    }}}],
                },
            }
            (output_dir / "video-id.live_chat.json").write_text(json.dumps(action) + "\n", encoding="utf-8")
            return {}

    monkeypatch.setattr("app.services.live_youtube_service.YoutubeDL", FakeYoutubeDL)

    result = collect_chat_replay("video-id", "chat-id")

    assert result["source"] == "yt_dlp_live_chat_replay"
    assert result["message_count"] == 1

    records = [
        json.loads(line)
        for line in (tmp_path / "yt-edit" / "chat-id" / "chat-replay.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert records[0]["elapsed_seconds"] == 75.0
    analysis = analyze_chat_archive("chat-id", bucket_seconds=30)
    assert analysis["buckets"][0]["start_seconds"] == 60

import json
from pathlib import Path

from app.services import youtube_importer
from app.services.youtube_importer import YouTubeImporter


class SubtitleRateLimitedYoutubeDL:
    calls = []

    def __init__(self, options):
        self.options = options
        self.job_dir = Path(options["outtmpl"]).parent

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, url, download=True):
        self.__class__.calls.append(self.options)

        if self.options["writesubtitles"]:
            raise RuntimeError(
                "ERROR: Unable to download video subtitles for 'en': "
                "HTTP Error 429: Too Many Requests"
            )

        video_path = self.job_dir / "sample-info-video.mp4"
        video_path.write_bytes(b"fake video")
        return {
            "title": "Sample info video",
            "duration": 90,
            "channel": "Sample Channel",
            "webpage_url": url,
            "requested_downloads": [{"filepath": str(video_path)}],
        }


def test_import_retries_without_subtitles_when_youtube_rate_limits_subtitles(
    tmp_path,
    monkeypatch,
    ):
    SubtitleRateLimitedYoutubeDL.calls = []
    monkeypatch.setattr(youtube_importer, "YoutubeDL", SubtitleRateLimitedYoutubeDL)
    monkeypatch.setattr(YouTubeImporter, "_has_ffmpeg", lambda self: True)
    importer = YouTubeImporter(tmp_path)

    result = importer._import_video_sync("https://www.youtube.com/watch?v=abc123")

    assert len(SubtitleRateLimitedYoutubeDL.calls) == 2
    assert SubtitleRateLimitedYoutubeDL.calls[0]["writesubtitles"] is True
    assert SubtitleRateLimitedYoutubeDL.calls[1]["writesubtitles"] is False
    assert result["title"] == "Sample info video"
    assert result["subtitle_files"] == []
    assert result["warnings"] == [
        "Subtitle download was rate-limited by YouTube. "
        "The video was imported without subtitles."
    ]

    metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
    assert metadata["warnings"] == result["warnings"]


class SingleFileYoutubeDL:
    calls = []

    def __init__(self, options):
        self.options = options
        self.job_dir = Path(options["outtmpl"]).parent

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, url, download=True):
        self.__class__.calls.append(self.options)
        video_path = self.job_dir / "single-file-video.mp4"
        video_path.write_bytes(b"fake video")
        return {
            "title": "Single file video",
            "duration": 45,
            "webpage_url": url,
            "requested_downloads": [{"filepath": str(video_path)}],
        }


def test_import_uses_single_file_format_when_ffmpeg_is_missing(tmp_path, monkeypatch):
    SingleFileYoutubeDL.calls = []
    monkeypatch.setattr(youtube_importer, "YoutubeDL", SingleFileYoutubeDL)
    monkeypatch.setattr(YouTubeImporter, "_has_ffmpeg", lambda self: False, raising=False)
    importer = YouTubeImporter(tmp_path)

    result = importer._import_video_sync("https://www.youtube.com/watch?v=abc123")

    assert SingleFileYoutubeDL.calls[0]["format"] == "best[ext=mp4]/best"
    assert "merge_output_format" not in SingleFileYoutubeDL.calls[0]
    assert result["title"] == "Single file video"
    assert result["warnings"] == [
        "ffmpeg is not installed. Downloaded a single-file video stream; "
        "quality may be lower."
    ]

    metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
    assert metadata["warnings"] == result["warnings"]

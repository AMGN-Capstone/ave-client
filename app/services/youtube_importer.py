import asyncio
import json
import shutil
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from app.config import get_media_root

try:
    from yt_dlp import YoutubeDL
except ImportError:  # pragma: no cover - exercised only when dependency is missing
    YoutubeDL = None


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}
SUBTITLE_EXTENSIONS = {".vtt", ".srt", ".json3"}
SUBTITLE_RATE_LIMIT_WARNING = (
    "Subtitle download was rate-limited by YouTube. "
    "The video was imported without subtitles."
)
FFMPEG_MISSING_WARNING = (
    "ffmpeg is not installed. Downloaded a single-file video stream; "
    "quality may be lower."
)


class InvalidYouTubeURLError(ValueError):
    pass


class YouTubeImportError(RuntimeError):
    pass


def is_youtube_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return parsed.scheme in {"http", "https"} and host in YOUTUBE_HOSTS


def relative_to_cwd(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


class YouTubeImporter:
    def __init__(self, media_root: Path | None = None):
        self.media_root = media_root or get_media_root()

    async def import_video(self, url: str) -> dict:
        return await asyncio.to_thread(self._import_video_sync, url)

    def _import_video_sync(self, url: str) -> dict:
        if not is_youtube_url(url):
            raise InvalidYouTubeURLError("Only YouTube URLs are supported.")
        if YoutubeDL is None:
            raise YouTubeImportError("yt-dlp is not installed.")

        job_id = uuid4().hex
        job_dir = self.media_root / "youtube" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        prefer_merged_formats = self._has_ffmpeg()
        warnings = []
        if not prefer_merged_formats:
            warnings.append(FFMPEG_MISSING_WARNING)

        ydl_options = self._build_ydl_options(
            job_dir,
            include_subtitles=True,
            prefer_merged_formats=prefer_merged_formats,
        )

        try:
            info = self._download(url, ydl_options)
        except Exception as exc:  # yt-dlp raises several custom exception types.
            if not self._is_subtitle_rate_limit_error(exc):
                raise YouTubeImportError(str(exc)) from exc

            warnings.append(SUBTITLE_RATE_LIMIT_WARNING)
            fallback_options = self._build_ydl_options(
                job_dir,
                include_subtitles=False,
                prefer_merged_formats=prefer_merged_formats,
            )
            try:
                info = self._download(url, fallback_options)
            except Exception as fallback_exc:
                raise YouTubeImportError(str(fallback_exc)) from fallback_exc

        subtitle_files = self._find_files(job_dir, SUBTITLE_EXTENSIONS)
        video_file = self._find_video_file(job_dir, info)
        metadata_path = self._write_metadata(
            job_dir,
            job_id,
            url,
            info,
            video_file,
            subtitle_files,
            warnings,
        )

        return {
            "job_id": job_id,
            "source_url": url,
            "title": info.get("title"),
            "duration": info.get("duration"),
            "video_path": relative_to_cwd(video_file) if video_file else None,
            "subtitle_files": [relative_to_cwd(path) for path in subtitle_files],
            "metadata_path": relative_to_cwd(metadata_path),
            "warnings": warnings,
        }

    def _build_ydl_options(
        self,
        job_dir: Path,
        include_subtitles: bool,
        prefer_merged_formats: bool,
    ) -> dict:
        options = {
            "format": "bv*+ba/best" if prefer_merged_formats else "best[ext=mp4]/best",
            "outtmpl": str(job_dir / "%(title).120s-%(id)s.%(ext)s"),
            "writeautomaticsub": include_subtitles,
            "writesubtitles": include_subtitles,
            "subtitleslangs": ["ko", "en"] if include_subtitles else [],
            "subtitlesformat": "vtt",
            "writeinfojson": True,
            "writethumbnail": True,
            "quiet": True,
            "no_warnings": True,
        }
        if prefer_merged_formats:
            options["merge_output_format"] = "mp4"
        return options

    def _download(self, url: str, ydl_options: dict) -> dict:
        with YoutubeDL(ydl_options) as downloader:
            return downloader.extract_info(url, download=True)

    def _has_ffmpeg(self) -> bool:
        return shutil.which("ffmpeg") is not None

    def _is_subtitle_rate_limit_error(self, exc: Exception) -> bool:
        message = str(exc)
        return (
            "Unable to download video subtitles" in message
            and ("429" in message or "Too Many Requests" in message)
        )

    def _write_metadata(
        self,
        job_dir: Path,
        job_id: str,
        url: str,
        info: dict,
        video_file: Path | None,
        subtitle_files: list[Path],
        warnings: list[str],
    ) -> Path:
        metadata = {
            "job_id": job_id,
            "source_url": url,
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "duration": info.get("duration"),
            "webpage_url": info.get("webpage_url"),
            "description": info.get("description"),
            "chapters": info.get("chapters") or [],
            "video_path": relative_to_cwd(video_file) if video_file else None,
            "subtitle_files": [relative_to_cwd(path) for path in subtitle_files],
            "warnings": warnings,
        }
        metadata_path = job_dir / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata_path

    def _find_video_file(self, job_dir: Path, info: dict) -> Path | None:
        for item in info.get("requested_downloads") or []:
            filepath = item.get("filepath")
            if filepath:
                candidate = Path(filepath)
                if candidate.exists():
                    return candidate

        files = self._find_files(job_dir, VIDEO_EXTENSIONS)
        if not files:
            return None
        return max(files, key=lambda path: path.stat().st_size)

    def _find_files(self, directory: Path, extensions: set[str]) -> list[Path]:
        return sorted(
            path
            for path in directory.glob("*")
            if path.is_file() and path.suffix.lower() in extensions
        )

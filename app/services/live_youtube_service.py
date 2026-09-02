"""YouTube live metadata, captions, and chat helpers."""

from __future__ import annotations

import json
import re
from threading import Lock
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from app.config import get_media_root
from app.services.ytdlp_binary import YoutubeDL


YOUTUBE_API_ROOT = "https://www.googleapis.com/youtube/v3"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
THUMBNAIL_FILENAMES = {"sddefault.jpg", "sd1.jpg", "sd2.jpg", "sd3.jpg"}


class LiveYouTubeError(RuntimeError):
    pass


_CHAT_ARCHIVE_LOCK = Lock()


def _chat_archive_path(live_chat_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", live_chat_id.removeprefix("vod-"))
    return get_media_root() / "yt-edit" / safe_id / "chat-replay.jsonl"


def _chat_session_path(live_chat_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", live_chat_id.removeprefix("vod-"))
    return get_media_root() / "yt-edit" / safe_id / "chat-session.json"


def _download_thumbnail_list(
    downloader,
    thumbnails: list[dict],
    main_thumbnail: str | None,
    output_dir: Path,
    video_id: str,
) -> list[dict]:
    """Save every thumbnail entry reported by yt-dlp, without deduplication."""

    thumbnail_dir = output_dir / "thumbnails"
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    for index, thumbnail in enumerate(thumbnails, start=1):
        if not isinstance(thumbnail, dict) or not isinstance(thumbnail.get("url"), str):
            continue
        # Store the filename yt-dlp exposes in the thumbnail URL unchanged.
        filename = Path(urlparse(thumbnail["url"]).path).name
        if filename not in THUMBNAIL_FILENAMES:
            continue
        is_primary = filename == "sddefault.jpg"
        path = thumbnail_dir / filename
        try:
            if not path.exists():
                response = downloader.urlopen(thumbnail["url"])
                try:
                    content = response.read()
                finally:
                    response.close()
                if not content:
                    continue
                path.write_bytes(content)
        except Exception:
            # An unavailable secondary thumbnail must not make metadata
            # inspection fail; the original URL remains in info.json.
            continue
        saved.append({
            "id": str(index),
            "url": f"/api/youtube/thumbnail/{video_id}/{filename}",
            "source_url": thumbnail["url"],
            "width": thumbnail.get("width"),
            "height": thumbnail.get("height"),
            "is_primary": is_primary,
        })
    if saved and not any(item["is_primary"] for item in saved):
        saved[0]["is_primary"] = True
    return saved


def _cached_thumbnail_files(output_dir: Path, video_id: str, main_thumbnail: str | None) -> list[dict]:
    """Expose previously saved thumbnail assets without contacting YouTube."""

    thumbnail_dir = output_dir / "thumbnails"
    if not thumbnail_dir.exists():
        return []
    files: list[dict] = []
    for path in sorted(thumbnail_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name not in THUMBNAIL_FILENAMES:
            continue
        files.append({
            "id": path.stem,
            "url": f"/api/youtube/thumbnail/{video_id}/{path.name}",
            "is_primary": path.name == "sddefault.jpg",
        })
    if files and not any(item["is_primary"] for item in files):
        files[0]["is_primary"] = True
    return files


def _save_chat_messages(live_chat_id: str, messages: list[dict]) -> Path:
    """Append newly received chat messages to a UTF-8 JSONL archive."""

    archive_path = _chat_archive_path(live_chat_id)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with _CHAT_ARCHIVE_LOCK:
        known_keys: set[str] = set()
        if archive_path.exists():
            with archive_path.open("r", encoding="utf-8") as archive_file:
                for line in archive_file:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    record_key = str(record.get("id") or "")
                    known_keys.add(record_key)

        with archive_path.open("a", encoding="utf-8") as archive_file:
            for message in messages:
                record = {
                    "id": message.get("id"),
                    "time": message.get("published_at"),
                    "message": message.get("display_message"),
                    "type": message.get("type"),
                    "super_chat": message.get("super_chat"),
                }
                record_key = str(record.get("id") or "")
                if not record_key:
                    record_key = f"{record['time']}|{record['type']}|{record['message']}"
                if record_key in known_keys:
                    continue
                archive_file.write(
                    json.dumps(record, ensure_ascii=False)
                    + "\n"
                )
                known_keys.add(record_key)

    return archive_path


def _save_replay_messages(archive_key: str, messages: list[dict]) -> Path:
    """Append normalized yt-dlp replay records to the JSONL archive."""

    archive_path = _chat_archive_path(archive_key)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with _CHAT_ARCHIVE_LOCK:
        known_ids: set[str] = set()
        if archive_path.exists():
            with archive_path.open("r", encoding="utf-8") as archive_file:
                for line in archive_file:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("id"):
                        known_ids.add(str(record["id"]))

        with archive_path.open("a", encoding="utf-8") as archive_file:
            for message in messages:
                message_id = str(message.get("id") or "")
                if message_id and message_id in known_ids:
                    continue
                archive_file.write(
                    json.dumps(message, ensure_ascii=False) + "\n"
                )
                if message_id:
                    known_ids.add(message_id)
    return archive_path


def _chat_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("simpleText"), str):
            return value["simpleText"]
        runs = value.get("runs")
        if isinstance(runs, list):
            return "".join(str(run.get("text", "")) for run in runs if isinstance(run, dict))
    return ""


def _normalize_ytdlp_replay_action(action: dict) -> list[dict]:
    replay = action.get("replayChatItemAction") if isinstance(action, dict) else None
    if not isinstance(replay, dict):
        return []
    try:
        elapsed_seconds = float(replay.get("videoOffsetTimeMsec")) / 1000
    except (TypeError, ValueError):
        return []
    messages: list[dict] = []
    for nested in replay.get("actions", []):
        item = nested.get("addChatItemAction", {}).get("item", {}) if isinstance(nested, dict) else {}
        if not isinstance(item, dict):
            continue
        renderer_name, renderer = next(
            ((name, value) for name, value in item.items() if name.endswith("Renderer") and isinstance(value, dict)),
            (None, None),
        )
        if renderer is None:
            continue
        message = _chat_text(renderer.get("message")) or _chat_text(renderer.get("headerSubtext"))
        messages.append({
            "id": renderer.get("id"),
            "elapsed_seconds": elapsed_seconds,
            "message": message,
            "type": renderer_name,
            "super_chat": _chat_text(renderer.get("purchaseAmountText")) or None,
        })
    return messages


def collect_chat_replay(video_id: str, archive_key: str) -> dict:
    """Download and normalize an archived live-chat replay with yt-dlp."""

    output_dir = get_media_root() / "yt-data" / video_id
    output_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": ["live_chat"],
        "subtitlesformat": "json",
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with YoutubeDL(options) as downloader:
            downloader.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
    except Exception as exc:
        raise LiveYouTubeError(f"yt-dlp 채팅 리플레이 수집에 실패했습니다: {exc}") from exc

    raw_files = sorted(output_dir.glob("*.live_chat.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not raw_files:
        raise LiveYouTubeError("yt-dlp가 채팅 리플레이 파일을 제공하지 않았습니다.")
    messages: list[dict] = []
    for line in raw_files[0].read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            messages.extend(_normalize_ytdlp_replay_action(json.loads(line)))
        except json.JSONDecodeError:
            continue
    if not messages:
        raise LiveYouTubeError("yt-dlp 채팅 리플레이에서 시간 정보가 있는 메시지를 읽지 못했습니다.")
    archive_path = _save_replay_messages(archive_key, messages)
    return {
        "source": "yt_dlp_live_chat_replay",
        "video_id": video_id,
        "archive_key": archive_key,
        "message_count": len(messages),
        "chat_file_path": str(archive_path.resolve()),
    }


def _write_chat_session(live_chat_id: str, values: dict) -> Path:
    session_path = _chat_session_path(live_chat_id)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    with _CHAT_ARCHIVE_LOCK:
        session_path.write_text(
            json.dumps(values, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return session_path


def _read_chat_session(live_chat_id: str) -> dict:
    path = _chat_session_path(live_chat_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_iso_duration_seconds(value: str | None) -> float | None:
    """Parse YouTube's simple ISO-8601 video duration without dependencies."""

    if not value or not value.startswith("PT"):
        return None
    match = re.fullmatch(
        r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?",
        value,
    )
    if not match:
        return None
    return (
        float(match.group("hours") or 0) * 3600
        + float(match.group("minutes") or 0) * 60
        + float(match.group("seconds") or 0)
    )
def _message_offset_seconds(
    published_at: str | None,
    actual_start_time: str | None,
    delay_seconds: float,
) -> float | None:
    message_time = _parse_datetime(published_at)
    start_time = _parse_datetime(actual_start_time)
    if not message_time or not start_time:
        return None
    return max(0.0, (message_time - start_time).total_seconds() - delay_seconds)


def _load_archived_messages(live_chat_id: str) -> list[dict]:
    archive_path = _chat_archive_path(live_chat_id)
    if not archive_path.exists():
        return []
    messages: list[dict] = []
    with archive_path.open("r", encoding="utf-8") as archive_file:
        for line in archive_file:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            messages.append(message)
    return messages


def analyze_chat_archive(
    live_chat_id: str,
    *,
    actual_start_time: str | None = None,
    duration_seconds: float | None = None,
    bucket_seconds: int = 30,
    delay_seconds: float = 0.0,
) -> dict:
    """Convert captured live chat into time buckets and highlight windows."""

    if bucket_seconds < 5 or bucket_seconds > 300:
        raise LiveYouTubeError("bucket_seconds는 5초에서 300초 사이여야 합니다.")

    session = _read_chat_session(live_chat_id)
    start_time = actual_start_time or session.get("actual_start_time")
    messages = _load_archived_messages(live_chat_id)
    buckets: dict[int, dict] = {}

    for message in messages:
        offset = message.get("elapsed_seconds")
        if offset is None:
            offset = _message_offset_seconds(
                message.get("time"), start_time, delay_seconds
            )
        if offset is None:
            continue
        if duration_seconds is not None and offset > duration_seconds:
            continue
        bucket_index = int(offset // bucket_seconds)
        bucket = buckets.setdefault(
            bucket_index,
            {
                "start_seconds": bucket_index * bucket_seconds,
                "end_seconds": (bucket_index + 1) * bucket_seconds,
                "message_count": 0,
                "text_message_count": 0,
                "event_count": 0,
                "super_chat_count": 0,
            },
        )
        bucket["message_count"] += 1
        if message.get("type") == "textMessageEvent":
            bucket["text_message_count"] += 1
        else:
            bucket["event_count"] += 1
        if message.get("super_chat"):
            bucket["super_chat_count"] += 1

    ordered = [buckets[index] for index in sorted(buckets)]
    average = (
        sum(bucket["message_count"] for bucket in ordered) / len(ordered)
        if ordered
        else 0.0
    )
    for bucket in ordered:
        bucket["messages_per_minute"] = round(
            bucket["message_count"] * 60 / bucket_seconds, 3
        )
        bucket["burst_score"] = round(
            min(1.0, bucket["message_count"] / max(average * 3, 1.0)), 3
        )

    peak_threshold = max(average * 1.5, 3.0)
    peak_buckets = [
        bucket for bucket in ordered if bucket["message_count"] >= peak_threshold
    ]
    windows: list[dict] = []
    padding = max(bucket_seconds, 30)
    for bucket in peak_buckets:
        start = max(0.0, bucket["start_seconds"] - padding)
        end = bucket["end_seconds"] + padding
        if duration_seconds is not None:
            end = min(float(duration_seconds), end)
        if windows and start <= windows[-1]["end_seconds"]:
            windows[-1]["end_seconds"] = max(windows[-1]["end_seconds"], end)
            windows[-1]["message_count"] += bucket["message_count"]
            windows[-1]["burst_score"] = max(
                windows[-1]["burst_score"], bucket["burst_score"]
            )
        else:
            windows.append(
                {
                    "start_seconds": round(start, 3),
                    "end_seconds": round(end, 3),
                    "message_count": bucket["message_count"],
                    "burst_score": bucket["burst_score"],
                }
            )

    return {
        "live_chat_id": live_chat_id,
        "actual_start_time": start_time,
        "duration_seconds": duration_seconds,
        "bucket_seconds": bucket_seconds,
        "delay_seconds": delay_seconds,
        "total_messages": len(messages),
        "average_messages_per_bucket": round(average, 3),
        "buckets": ordered,
        "highlight_windows": windows,
    }


def extract_video_id(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().split(":", 1)[0]
    video_id = ""

    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        query_id = parse_qs(parsed.query).get("v", [""])[0]
        if parsed.path == "/watch":
            video_id = query_id
        elif parsed.path.startswith("/live/"):
            video_id = parsed.path.split("/", 2)[2].split("/", 1)[0]
        elif parsed.path.startswith("/embed/"):
            video_id = parsed.path.split("/", 2)[2].split("/", 1)[0]

    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise LiveYouTubeError("유효한 YouTube 영상 URL이 아닙니다.")
    return video_id


def _youtube_get(path: str, access_token: str, params: dict) -> dict:
    try:
        import requests
    except ImportError as exc:
        raise LiveYouTubeError(
            "requests가 설치되어 있지 않습니다. requirements.txt를 설치하세요."
        ) from exc

    if not access_token.strip():
        raise LiveYouTubeError(
            "YouTube OAuth access token이 없습니다. Google 로그인 시 YouTube 읽기 권한을 허용하세요."
        )

    response = requests.get(
        f"{YOUTUBE_API_ROOT}/{path}",
        params=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if not response.ok:
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            detail = response.text
        raise LiveYouTubeError(f"YouTube API 오류 ({response.status_code}): {detail}")
    return response.json()


def get_live_broadcast(access_token: str, video_id: str) -> dict:
    """Read public live-video metadata without broadcaster-only permissions.

    ``liveBroadcasts.list`` is intended for the broadcaster's channel and
    returns ``liveStreamingNotEnabled`` for ordinary viewer accounts. The
    public ``videos.list`` resource exposes the active live chat ID and live
    metadata without requiring the signed-in user to be a broadcaster.
    """

    body = _youtube_get(
        "videos",
        access_token,
        {"part": "snippet,liveStreamingDetails,statistics", "id": video_id},
    )
    items = body.get("items", [])
    if not items:
        raise LiveYouTubeError("YouTube 영상을 찾지 못했습니다.")

    item = items[0]
    snippet = item.get("snippet", {})
    live_details = item.get("liveStreamingDetails", {})
    live_broadcast_content = snippet.get("liveBroadcastContent", "none")
    is_live_recording = bool(
        live_details.get("actualStartTime")
        or live_details.get("actualEndTime")
        or live_broadcast_content in {"live", "upcoming"}
    )
    if not is_live_recording:
        raise LiveYouTubeError("라이브 방송 다시보기를 찾지 못했습니다.")

    if live_broadcast_content == "live":
        lifecycle_status = "live"
    elif live_broadcast_content == "upcoming":
        lifecycle_status = "upcoming"
    else:
        lifecycle_status = "completed"
    return {
        "video_id": item.get("id", video_id),
        "title": snippet.get("title"),
        "channel_id": snippet.get("channelId"),
        "published_at": snippet.get("publishedAt"),
        "actual_start_time": live_details.get("actualStartTime"),
        "actual_end_time": live_details.get("actualEndTime"),
        "scheduled_start_time": live_details.get("scheduledStartTime"),
        "life_cycle_status": lifecycle_status,
        "concurrent_viewers": live_details.get("concurrentViewers"),
        "live_chat_id": live_details.get("activeLiveChatId"),
        "chat_capture_available": bool(live_details.get("activeLiveChatId")),
        "chat_capture_required_during_live": not bool(live_details.get("activeLiveChatId")),
        "view_count": item.get("statistics", {}).get("viewCount"),
    }


def get_live_chat(
    access_token: str,
    live_chat_id: str,
    page_token: str | None = None,
    lifecycle_status: str | None = None,
    actual_start_time: str | None = None,
    delay_seconds: float = 0.0,
) -> dict:
    if not live_chat_id:
        raise LiveYouTubeError("라이브 채팅 ID가 없습니다.")
    params = {
        "part": "id,snippet,authorDetails",
        "liveChatId": live_chat_id,
        "maxResults": 2000,
    }
    if page_token:
        params["pageToken"] = page_token

    body = _youtube_get("liveChat/messages", access_token, params)
    messages = []
    for item in body.get("items", []):
        snippet = item.get("snippet", {})
        messages.append(
            {
                "id": item.get("id"),
                "type": snippet.get("type"),
                "published_at": snippet.get("publishedAt"),
                "display_message": snippet.get("displayMessage"),
                "super_chat": snippet.get("superChatDetails"),
            }
        )

    archive_path = _save_chat_messages(live_chat_id, messages)

    session = _read_chat_session(live_chat_id)
    session.update(
        {
            "live_chat_id": live_chat_id,
            "actual_start_time": actual_start_time or session.get("actual_start_time"),
            "last_collected_at": messages[-1].get("published_at") if messages else session.get("last_collected_at"),
            "chat_file_path": str(archive_path.resolve()),
        }
    )
    _write_chat_session(live_chat_id, session)
    analysis = analyze_chat_archive(
        live_chat_id,
        actual_start_time=session.get("actual_start_time"),
        delay_seconds=delay_seconds,
    )

    return {
        "messages": messages,
        "next_page_token": body.get("nextPageToken"),
        "polling_interval_millis": body.get("pollingIntervalMillis", 5000),
        "offline_at": body.get("offlineAt"),
        "chat_file_path": str(archive_path.resolve()),
        "total_messages": analysis["total_messages"],
        "highlight_windows": analysis["highlight_windows"],
    }


def get_video_metadata(url: str) -> dict:
    """Read and persist phase-one public metadata through yt-dlp."""

    thumbnail_files: list[dict] = []
    video_id = extract_video_id(url)
    output_dir = get_media_root() / "yt-data" / video_id
    output_dir.mkdir(parents=True, exist_ok=True)
    info_path = output_dir / f"{video_id}.info.json"
    info: dict | None = None
    if info_path.exists():
        try:
            cached_info = json.loads(info_path.read_text(encoding="utf-8"))
            if isinstance(cached_info, dict):
                info = cached_info
                thumbnail_files = _cached_thumbnail_files(output_dir, video_id, info.get("thumbnail"))
                # Older caches predate the thumbnails directory. Reuse their
                # info JSON and only backfill missing image assets; metadata
                # extraction itself is not repeated.
                if not thumbnail_files and info.get("thumbnails"):
                    try:
                        with YoutubeDL({"quiet": True, "no_warnings": True}) as downloader:
                            thumbnail_files = _download_thumbnail_list(
                                downloader,
                                info["thumbnails"],
                                info.get("thumbnail"),
                                output_dir,
                                str(info.get("id") or video_id),
                            )
                    except Exception:
                        pass
        except (OSError, json.JSONDecodeError):
            pass

    if info is None:
        try:
            with YoutubeDL({
                "skip_download": True,
                "writeinfojson": True,
                "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
            }) as downloader:
                # download=True persists the unmodified yt-dlp info JSON.
                info = downloader.extract_info(url, download=True)
                if isinstance(info, dict):
                    thumbnail_files = _download_thumbnail_list(
                        downloader,
                        info.get("thumbnails") or [],
                        info.get("thumbnail"),
                        output_dir,
                        str(info.get("id") or video_id),
                    )
        except Exception as exc:
            raise LiveYouTubeError(f"YouTube 메타데이터를 가져오지 못했습니다: {exc}") from exc

    if not isinstance(info, dict):
        raise LiveYouTubeError("YouTube 메타데이터 형식이 올바르지 않습니다.")

    subtitles = info.get("subtitles") or {}
    automatic_captions = info.get("automatic_captions") or {}
    # yt-dlp places a `live_chat` pseudo-subtitle in `subtitles`. It is a
    # chat stream, not a user-uploaded subtitle track.
    uploaded_subtitles = {
        language: tracks
        for language, tracks in subtitles.items()
        if language != "live_chat"
    }
    live_chat_tracks = subtitles.get("live_chat") or []
    live_status = str(info.get("live_status") or "")
    chat_replay = info.get("chat_replay")
    if not isinstance(chat_replay, bool):
        chat_replay = any(
            isinstance(track, dict)
            and track.get("protocol") == "youtube_live_chat_replay"
            for track in live_chat_tracks
        )
        if not chat_replay:
            # The YouTube extractor does not consistently expose a separate
            # `chat_replay` key. A completed livestream is the yt-dlp signal
            # that a replay chat can be requested; the editing job still
            # verifies it before using the messages.
            chat_replay = live_status in {"was_live", "post_live"}
    metadata = {
        "source_url": url,
        "thumbnail": next(
            (item["url"] for item in thumbnail_files if item.get("is_primary")),
            info.get("thumbnail"),
        ),
        "thumbnails": info.get("thumbnails") or [],
        "thumbnail_files": thumbnail_files,
        "title": info.get("title"),
        "video_id": info.get("id"),
        "description": info.get("description"),
        "channel": info.get("channel") or info.get("uploader"),
        "upload_date": info.get("upload_date"),
        "duration_seconds": info.get("duration"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "categories": info.get("categories") or [],
        "tags": info.get("tags") or [],
        "subtitles_available": bool(uploaded_subtitles),
        "captions_available": bool(automatic_captions),
        "chapters": info.get("chapters") or [],
        "heatmap": info.get("heatmap") or [],
        "chat_replay_available": chat_replay,
        "live_status": live_status or None,
        # Compatibility fields used by the legacy live-finalization response.
        "duration_iso": info.get("duration_string"),
        "live_details": {"live_status": live_status} if live_status else {},
    }
    info_path = output_dir / f"{video_id}.info.json"
    return {**metadata, "metadata_path": str(info_path.resolve())}


def download_live_captions(url: str) -> dict:
    """Ask yt-dlp for currently exposed live captions without downloading video."""

    video_id = extract_video_id(url)
    job_dir = get_media_root() / "yt-data" / video_id
    job_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        # Request Korean first. Optional English captions must not block the
        # rest of the live-metadata response when YouTube rate-limits them.
        "subtitleslangs": ["ko"],
        "subtitlesformat": "vtt",
        "outtmpl": str(job_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }

    warnings: list[str] = []
    info: dict = {}
    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
    except Exception as exc:
        message = str(exc)
        if "429" in message or "Too Many Requests" in message:
            warnings.append(
                "YouTube가 자막 요청을 일시적으로 제한했습니다(HTTP 429). "
                "로컬 Whisper 대체 처리는 지원하지 않습니다. AVE Whisper API를 사용하세요."
            )
        else:
            warnings.append(f"자막을 가져오지 못했습니다: {message}")

    subtitle_files = sorted(job_dir.glob("*.vtt"))
    subtitle_text = ""
    if subtitle_files:
        subtitle_text = subtitle_files[0].read_text(
            encoding="utf-8-sig", errors="replace"
        )

    return {
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "is_live": info.get("is_live"),
        "duration": info.get("duration"),
        "subtitle_files": [str(path.resolve()) for path in subtitle_files],
        "subtitle_text": subtitle_text,
        "warnings": warnings,
    }



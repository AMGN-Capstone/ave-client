"""YouTube live metadata, captions, and chat helpers."""

from __future__ import annotations

import json
import re
import time
from threading import Lock
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from app.config import get_media_root


YOUTUBE_API_ROOT = "https://www.googleapis.com/youtube/v3"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


class LiveYouTubeError(RuntimeError):
    pass


_CHAT_ARCHIVE_LOCK = Lock()


def _chat_archive_path(live_chat_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", live_chat_id)
    return get_media_root() / "youtube-live-chat" / f"{safe_id}.jsonl"


def _chat_session_path(live_chat_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", live_chat_id)
    return get_media_root() / "youtube-live-chat" / f"{safe_id}.session.json"


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


def _parse_elapsed_time(value: str | None) -> float | None:
    """Convert pytchat's replay-only HH:MM:SS elapsed time to seconds."""

    if not value:
        return None
    try:
        parts = [int(part) for part in str(value).split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes * 60 + seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours * 3600 + minutes * 60 + seconds)
    return None


def _save_replay_messages(archive_key: str, messages: list[dict]) -> Path:
    """Append pytchat replay records to the same normalized JSONL archive."""

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


def collect_chat_replay(video_id: str, archive_key: str) -> dict:
    """Collect an archived live-chat replay through pytchat.

    pytchat exposes ``elapsedTime`` for replay messages, which is preferable
    to calculating an offset from wall-clock timestamps because it already
    follows the VOD playback timeline.
    """

    try:
        import pytchat
    except ImportError as exc:
        raise LiveYouTubeError(
            "pytchat이 설치되어 있지 않습니다. requirements.txt를 설치하세요."
        ) from exc

    messages: list[dict] = []
    chat = None
    try:
        # pytchat registers SIGINT by default. The collector runs in a worker
        # thread during the FastAPI request, so disable that signal hook and
        # let the request's finally block terminate the client instead.
        chat = pytchat.create(
            video_id=video_id,
            force_replay=True,
            interruptable=False,
        )
        while chat.is_alive():
            data = chat.get()
            for item in getattr(data, "items", []):
                elapsed_seconds = _parse_elapsed_time(
                    getattr(item, "elapsedTime", None)
                )
                if elapsed_seconds is None:
                    continue
                messages.append(
                    {
                        "id": getattr(item, "id", None),
                        "time": getattr(item, "datetime", None),
                        "elapsed_seconds": elapsed_seconds,
                        "message": getattr(item, "message", ""),
                        "type": getattr(item, "type", "textMessage"),
                        "super_chat": {
                            "amount": getattr(item, "amountString", ""),
                            "currency": getattr(item, "currency", ""),
                        }
                        if getattr(item, "amountString", "")
                        else None,
                    }
                )
            time.sleep(0.1)
    except Exception as exc:
        raise LiveYouTubeError(f"pytchat 다시보기 수집에 실패했습니다: {exc}") from exc
    finally:
        if chat is not None:
            try:
                chat.terminate()
            except Exception:
                pass

    archive_path = _save_replay_messages(archive_key, messages)
    return {
        "source": "pytchat_replay",
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


def get_video_metadata(access_token: str, video_id: str) -> dict:
    body = _youtube_get(
        "videos",
        access_token,
        {"part": "snippet,contentDetails,liveStreamingDetails", "id": video_id},
    )
    items = body.get("items", [])
    if not items:
        raise LiveYouTubeError("다시보기 영상을 찾지 못했습니다.")
    item = items[0]
    duration_iso = item.get("contentDetails", {}).get("duration")
    return {
        "video_id": item.get("id", video_id),
        "title": item.get("snippet", {}).get("title"),
        "duration_iso": duration_iso,
        "duration_seconds": parse_iso_duration_seconds(duration_iso),
        "live_details": item.get("liveStreamingDetails", {}),
    }


def download_live_captions(url: str) -> dict:
    """Ask yt-dlp for currently exposed live captions without downloading video."""

    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise LiveYouTubeError("yt-dlp가 설치되어 있지 않습니다.") from exc

    job_dir = get_media_root() / "youtube-live" / uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        # Request Korean first. Optional English captions must not block the
        # rest of the live-metadata response when YouTube rate-limits them.
        "subtitleslangs": ["ko"],
        "subtitlesformat": "vtt",
        "outtmpl": str(job_dir / "%(title).120s-%(id)s.%(ext)s"),
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
                "오디오를 내려받아 faster-whisper 로컬 STT를 시도합니다."
            )
            try:
                fallback = _transcribe_downloaded_audio(url, job_dir)
                return {
                    "title": info.get("title") or fallback.get("title"),
                    "channel": info.get("channel") or fallback.get("channel"),
                    "is_live": info.get("is_live"),
                    "duration": info.get("duration") or fallback.get("duration"),
                    "subtitle_files": [],
                    "subtitle_text": fallback["subtitle_text"],
                    "transcript": fallback["transcript"],
                    "warnings": warnings + fallback.get("warnings", []),
                }
            except Exception as fallback_exc:
                warnings.append(f"로컬 STT도 실패했습니다: {fallback_exc}")
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


def _transcribe_downloaded_audio(url: str, job_dir: Path) -> dict:
    """Download an audio-only source and transcribe it locally."""

    try:
        from yt_dlp import YoutubeDL
        from app.services.local_video_transcriber import transcript_as_text
        from app.services.transcription_service import transcribe_media
    except ImportError as exc:
        raise LiveYouTubeError(
            "로컬 STT를 사용하려면 yt-dlp와 faster-whisper를 설치하세요."
        ) from exc

    audio_dir = job_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(audio_dir / "source.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)

    audio_files = [path for path in audio_dir.glob("source.*") if path.is_file()]
    if not audio_files:
        raise LiveYouTubeError("오디오 파일을 찾지 못했습니다.")

    transcript = transcribe_media(audio_files[0])
    return {
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "subtitle_text": transcript_as_text(transcript),
        "transcript": transcript,
        "warnings": [],
    }

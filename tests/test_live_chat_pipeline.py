import json
from datetime import datetime, timedelta, timezone

from app.services.live_youtube_service import analyze_chat_archive, _chat_archive_path


def test_chat_archive_is_converted_to_time_buckets_and_peaks(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    start = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    archive = _chat_archive_path("chat-test")
    archive.parent.mkdir(parents=True, exist_ok=True)

    records = []
    for index in range(4):
        records.append(
            {
                "id": f"normal-{index}",
                "time": (start + timedelta(seconds=15)).isoformat().replace("+00:00", "Z"),
                "message": "normal",
                "type": "textMessageEvent",
            }
        )
    for index in range(12):
        records.append(
            {
                "id": f"peak-{index}",
                "time": (start + timedelta(seconds=75)).isoformat().replace("+00:00", "Z"),
                "message": "peak",
                "type": "textMessageEvent",
            }
        )
    archive.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    result = analyze_chat_archive(
        "chat-test",
        actual_start_time=start.isoformat().replace("+00:00", "Z"),
        bucket_seconds=30,
    )

    assert result["total_messages"] == 16
    bucket_by_start = {item["start_seconds"]: item for item in result["buckets"]}
    assert bucket_by_start[0]["message_count"] == 4
    assert bucket_by_start[60]["message_count"] == 12
    assert result["highlight_windows"]
    assert result["highlight_windows"][0]["start_seconds"] == 30.0

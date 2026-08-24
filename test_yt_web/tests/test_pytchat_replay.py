import json
import sys
from types import SimpleNamespace

from app.services.live_youtube_service import (
    analyze_chat_archive,
    collect_chat_replay,
)


class FakeChat:
    def __init__(self, items):
        self.items = items
        self.calls = 0
        self.terminated = False

    def is_alive(self):
        return self.calls < 1

    def get(self):
        self.calls += 1
        return SimpleNamespace(items=self.items)

    def terminate(self):
        self.terminated = True


def test_pytchat_replay_is_saved_with_elapsed_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    fake_items = [
        SimpleNamespace(
            id="replay-1",
            elapsedTime="00:01:15",
            datetime="2026-08-18 12:01:15",
            message="peak",
            type="textMessage",
            amountString="",
            currency="",
        )
    ]
    fake_chat = FakeChat(fake_items)
    fake_module = SimpleNamespace(
        create=lambda video_id, force_replay, interruptable: fake_chat,
    )
    monkeypatch.setitem(sys.modules, "pytchat", fake_module)

    result = collect_chat_replay("video-id", "chat-id")

    assert result["source"] == "pytchat_replay"
    assert result["message_count"] == 1
    assert fake_chat.terminated is True

    records = [
        json.loads(line)
        for line in (tmp_path / "youtube-live-chat" / "chat-id.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert records[0]["elapsed_seconds"] == 75.0
    analysis = analyze_chat_archive("chat-id", bucket_seconds=30)
    assert analysis["buckets"][0]["start_seconds"] == 60

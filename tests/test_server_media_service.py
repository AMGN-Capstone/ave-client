from app.services import server_media_service


class _Response:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_lines(self, decode_unicode=False):
        yield 'data: {"status":"completed","progress":100,"message":"완료","result":{"segments":[]}}'

    def close(self):
        return None


def test_wait_for_transcription_sends_heartbeat_before_status(monkeypatch):
    posts = []
    monkeypatch.setattr(server_media_service, "_server_url", lambda: "https://server.example")
    monkeypatch.setattr(server_media_service, "get_whisper_heartbeat_seconds", lambda: 10)
    monkeypatch.setattr(
        server_media_service.requests,
        "post",
        lambda url, **kwargs: posts.append((url, kwargs)) or _Response(),
    )
    monkeypatch.setattr(
        server_media_service.requests,
        "get",
        lambda url, **kwargs: _Response({"status": "completed", "progress": 100, "message": "완료", "result": {"segments": []}}),
    )

    result = server_media_service._wait_for_transcription("runpod-001", "Bearer token", None)

    assert result == {"segments": []}
    assert posts[0][0].endswith("/api/stt/transcriptions/runpod-001/heartbeat")
    assert posts[0][1]["headers"]["Authorization"] == "Bearer token"


def test_wait_for_transcription_reconnects_after_temporary_sse_disconnect(monkeypatch):
    attempts = []
    messages = []
    monkeypatch.setattr(server_media_service, "_server_url", lambda: "https://server.example")
    monkeypatch.setattr(server_media_service, "get_whisper_heartbeat_seconds", lambda: 10)
    monkeypatch.setattr(server_media_service.requests, "post", lambda *args, **kwargs: _Response())

    def get(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) == 1:
            raise server_media_service.requests.ConnectionError("connection reset")
        return _Response()

    monkeypatch.setattr(server_media_service.requests, "get", get)
    monkeypatch.setattr(server_media_service.time, "sleep", lambda _: None)

    result = server_media_service._wait_for_transcription("runpod-003", "Bearer token", lambda progress, message: messages.append((progress, message)))

    assert result == {"segments": []}
    assert len(attempts) == 2
    assert messages[0][0] == 0
    assert "다시 연결" in messages[0][1]


def test_cancel_uploaded_transcription_calls_server_cancel(monkeypatch):
    calls = []
    monkeypatch.setattr(server_media_service, "_server_url", lambda: "https://server.example")
    monkeypatch.setattr(
        server_media_service.requests,
        "post",
        lambda url, **kwargs: calls.append((url, kwargs)) or _Response(),
    )

    server_media_service.cancel_uploaded_transcription("runpod-002", "Bearer token")

    assert calls[0][0].endswith("/api/stt/transcriptions/runpod-002/cancel")
    assert calls[0][1]["headers"]["Authorization"] == "Bearer token"

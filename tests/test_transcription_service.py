from app.services import transcription_service


def test_groq_transcript_is_normalized_to_editor_segments(tmp_path, monkeypatch):
    media = tmp_path / "source.mp3"
    media.write_bytes(b"audio")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "language": "ko",
                "duration": 7.25,
                "segments": [
                    {"start": 1, "end": 3.5, "text": "  첫 문장  "},
                    {"start": "bad", "end": 5, "text": "무시"},
                ],
            }

    monkeypatch.setattr(transcription_service.requests, "post", lambda *_args, **_kwargs: FakeResponse())

    result = transcription_service.transcribe_media(media, provider="groq")

    assert result["duration"] == 7.25
    assert result["segments"] == [
        {"start": 1.0, "end": 3.5, "text": "첫 문장", "words": []}
    ]

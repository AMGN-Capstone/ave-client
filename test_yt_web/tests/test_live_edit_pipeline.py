import json

from app.services.live_edit_pipeline import (
    _ensure_opening_and_ending,
    parse_vtt,
    score_chat_density,
    write_selected_subtitles,
)


def test_parse_vtt_keeps_timestamps_and_removes_markup(tmp_path):
    path = tmp_path / "captions.ko.vtt"
    path.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:04.500\n<c>첫 번째 문장</c>\n\n"
        "00:00:05.000 --> 00:00:08.000\n두 번째 문장\n",
        encoding="utf-8",
    )

    result = parse_vtt(path)

    assert result == [
        {"start": 1.0, "end": 4.5, "text": "첫 번째 문장"},
        {"start": 5.0, "end": 8.0, "text": "두 번째 문장"},
    ]


def test_chat_density_uses_replay_elapsed_seconds():
    segments = [
        {"start": 0.0, "end": 10.0, "text": "일반 구간"},
        {"start": 60.0, "end": 70.0, "text": "반응 구간"},
    ]
    messages = [
        {"elapsed_seconds": 65.0, "message": f"반응 {index}", "super_chat": None}
        for index in range(12)
    ]

    result = score_chat_density(segments, messages, bucket_seconds=30)

    assert result[0]["chat_count"] == 0
    assert result[1]["chat_count"] == 12
    assert result[1]["chat_score"] > result[0]["chat_score"]


def test_selected_subtitles_are_remapped_to_edit_timeline(tmp_path):
    output = tmp_path / "subtitles.srt"
    count = write_selected_subtitles(
        [
            {"start": 10.0, "end": 15.0, "text": "첫 번째 구간"},
            {"start": 30.0, "end": 35.0, "text": "두 번째 구간"},
        ],
        [
            {"start": 10.0, "end": 15.0},
            {"start": 30.0, "end": 35.0},
        ],
        output,
    )

    assert count == 2
    content = output.read_text(encoding="utf-8-sig")
    assert "00:00:00,000 --> 00:00:05,000" in content
    assert "00:00:05,000 --> 00:00:10,000" in content


def test_opening_and_ending_are_added_to_selected_clips():
    result = _ensure_opening_and_ending(
        [{"start": 40.0, "end": 50.0, "final_score": 900}],
        [
            {"start": 5.0, "end": 15.0, "final_score": 700},
            {"start": 40.0, "end": 50.0, "final_score": 900},
            {"start": 95.0, "end": 105.0, "final_score": 800},
        ],
        100.0,
    )

    assert [item["start"] for item in result] == [5.0, 40.0, 95.0]
    assert result[0]["edit_role"] == "opening"
    assert result[-1]["edit_role"] == "ending"


def test_ai_news_intro_phrase_is_preferred_for_opening():
    result = _ensure_opening_and_ending(
        [],
        [
            {"start": 5.0, "end": 15.0, "text": "오늘 방송을 시작하겠습니다.", "final_score": 300},
            {"start": 20.0, "end": 30.0, "text": "중요한 기술 소식입니다.", "final_score": 900},
            {"start": 95.0, "end": 105.0, "text": "오늘 내용을 정리하겠습니다.", "final_score": 800},
        ],
        100.0,
        genre="ai_news",
    )

    assert result[0]["text"] == "오늘 방송을 시작하겠습니다."
    assert result[0]["edit_role"] == "opening"

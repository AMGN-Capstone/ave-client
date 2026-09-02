import json

from app.services.live_edit_pipeline import (
    LiveEditPipeline,
    LiveEditPipelineError,
    _build_candidate_chapters,
    _ensure_opening_and_ending,
    parse_vtt,
    render_preview,
    score_chat_density,
    write_selected_subtitles,
)


def _write_saved_review_job(tmp_path, job_id="review-job"):
    output_dir = tmp_path / "yt-edit" / job_id
    output_dir.mkdir(parents=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    candidates = [
        {
            "segment_id": "segment-a",
            "start": 10.0,
            "end": 20.0,
            "text": "첫 번째 후보",
            "llm_score": 800,
            "chat_score": 100,
            "final_score": 600,
            "reason": "핵심 설명",
        },
        {
            "segment_id": "segment-b",
            "start": 30.0,
            "end": 40.0,
            "text": "제외할 후보",
            "llm_score": 300,
            "chat_score": 50,
            "final_score": 210,
            "reason": "반복 설명",
        },
        {
            "segment_id": "segment-c",
            "start": 50.0,
            "end": 60.0,
            "text": "세 번째 후보",
            "llm_score": 900,
            "chat_score": 700,
            "final_score": 820,
            "reason": "결론",
        },
    ]
    plan = {
        "genre": "ai_news",
        "target_seconds": 20,
        "source_video_path": str(source),
        "source_duration_seconds": 100.0,
        "render_mode": "preview",
        "subtitle_offset_seconds": 0.0,
        "subtitle_font_name": "Malgun Gothic",
        "subtitle_font_size": 18,
        "candidates": candidates,
        "recommended_segment_ids": ["segment-a", "segment-c"],
        "selected_segment_ids": ["segment-a"],
        "clips": [{"start": 9.6, "end": 20.6}],
        "revision": 0,
    }
    (output_dir / "edit_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "cleaned_transcript.json").write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 10.0, "end": 20.0, "text": "첫 번째 후보"},
                    {"start": 30.0, "end": 40.0, "text": "제외할 후보"},
                    {"start": 50.0, "end": 60.0, "text": "세 번째 후보"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (output_dir / "subtitles.srt").write_text("기존 자막", encoding="utf-8")
    (output_dir / "edited-preview.mp4").write_bytes(b"old-render")
    return output_dir, plan


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


def test_parse_vtt_ports_scripter_postprocessing_for_noise_and_duplicate_cues(tmp_path):
    path = tmp_path / "captions.ko.vtt"
    path.write_text(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.000\n[박수] 첫 문장 (웃음)\n\n"
        "00:00:02.000 --> 00:00:03.000\n첫 문장\n\n"
        "00:00:03.000 --> 00:00:04.000\n[기침] (웃음)\n\n"
        "00:00:04.000 --> 00:00:05.000\n두 번째 문장\n",
        encoding="utf-8",
    )

    result = parse_vtt(path)

    assert result == [
        {"start": 1.0, "end": 3.0, "text": "첫 문장"},
        {"start": 4.0, "end": 5.0, "text": "두 번째 문장"},
    ]


def test_parse_vtt_removes_youtube_rolling_caption_overlap_without_llm(tmp_path):
    path = tmp_path / "rolling.ko.vtt"
    path.write_text(
        "WEBVTT\n\n"
        "00:00:09.639 --> 00:00:17.070 align:start position:0%\n\n"
        "아,<00:00:10.000><c> 안녕하세요.</c>\n\n"
        "00:00:20.509 --> 00:00:20.519 align:start position:0%\n"
        "아, 5.6 풀렸나요? 풀리긴 했는데\n\n"
        "00:00:20.519 --> 00:00:23.029 align:start position:0%\n"
        "아, 5.6 풀렸나요? 풀리긴 했는데 이제 소수의 기업들에게만 풀렸습니다.\n\n"
        "00:00:23.029 --> 00:00:23.039 align:start position:0%\n"
        "이제 소수의 기업들에게만 풀렸습니다.\n\n"
        "00:00:23.039 --> 00:00:26.189 align:start position:0%\n"
        "이제 소수의 기업들에게만 풀렸습니다. 네. 승인된 한 20여의 기업에게\n",
        encoding="utf-8",
    )

    result = parse_vtt(path)

    assert result == [
        {"start": 9.639, "end": 17.07, "text": "아, 안녕하세요."},
        {
            "start": 20.519,
            "end": 23.029,
            "text": "아, 5.6 풀렸나요? 풀리긴 했는데 이제 소수의 기업들에게만 풀렸습니다.",
        },
        {"start": 23.039, "end": 26.189, "text": "네. 승인된 한 20여의 기업에게"},
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

    result = score_chat_density(segments, messages)

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


def test_preview_reencodes_each_clip_from_an_accurate_seek(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    commands = []
    progress = []
    monkeypatch.setattr("app.services.live_edit_pipeline.shutil.which", lambda _name: "ffmpeg")
    monkeypatch.setattr("app.services.live_edit_pipeline._run_ffmpeg", commands.append)
    monkeypatch.setattr("app.services.live_edit_pipeline.shutil.copyfile", lambda _source, _output: None)

    render_preview(
        source,
        [{"start": 12.5, "end": 18.0}],
        tmp_path / "preview.mp4",
        progress_callback=progress.append,
    )

    clip_command = commands[0]
    assert clip_command.index("-i") < clip_command.index("-ss")
    assert clip_command[clip_command.index("-c:v") + 1] == "libx264"
    assert "-c" not in clip_command
    assert progress == [0.75, 0.82, 1.0]


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


def test_segment_review_exposes_stable_ids_and_selection(tmp_path):
    _write_saved_review_job(tmp_path)

    result = LiveEditPipeline(tmp_path).get_segment_review("review-job")

    assert result["selected_segment_ids"] == ["segment-a"]
    assert result["recommended_segment_ids"] == ["segment-a", "segment-c"]
    assert [item["segment_id"] for item in result["segments"]] == [
        "segment-a",
        "segment-b",
        "segment-c",
    ]
    assert [item["selected"] for item in result["segments"]] == [True, False, False]
    assert len(result["chapters"]) == 1
    assert result["chapters"][0]["segment_ids"] == [
        "segment-a",
        "segment-b",
        "segment-c",
    ]
    assert {item["chapter_id"] for item in result["segments"]} == {"chapter-00"}
    assert result["source_video_url"].endswith("/review-job/media/source")


def test_segment_review_rebuilds_malformed_stored_chapters(tmp_path):
    output_dir, plan = _write_saved_review_job(tmp_path)
    plan["chapters"] = [
        None,
        {
            "chapter_id": "bad-cover",
            "title": "손상된 저장값",
            "summary": "모든 ID를 포함하지만 다른 항목이 잘못됨",
            "start": 10.0,
            "end": 60.0,
            "segment_ids": ["segment-a", "segment-b", "segment-c"],
        },
    ]
    (output_dir / "edit_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False),
        encoding="utf-8",
    )

    result = LiveEditPipeline(tmp_path).get_segment_review("review-job")

    assert [chapter["chapter_id"] for chapter in result["chapters"]] == ["chapter-00"]
    assert result["chapters"][0]["segment_ids"] == [
        "segment-a",
        "segment-b",
        "segment-c",
    ]


def test_candidate_chapters_cover_every_segment_once_and_keep_the_last_summary():
    candidates = [
        {
            "segment_id": f"segment-{index:04d}",
            "start": float(index * 10),
            "end": float(index * 10 + 8),
            "text": f"후보 {index}",
        }
        for index in range(37)
    ]
    summary = {
        "chapters": [
            {"title": f"주제 {index}", "summary": f"요약 {index}"}
            for index in range(20)
        ]
    }

    chapters = _build_candidate_chapters(
        candidates,
        summary,
        max_chapters=12,
        max_segments_per_chapter=4,
    )

    flattened = [segment_id for chapter in chapters for segment_id in chapter["segment_ids"]]
    assert len(chapters) == 12
    assert flattened == [item["segment_id"] for item in candidates]
    assert len(flattened) == len(set(flattened))
    assert "주제 19" in chapters[-1]["title"]
    assert chapters == _build_candidate_chapters(
        candidates,
        summary,
        max_chapters=12,
        max_segments_per_chapter=4,
    )


def test_candidate_chapters_handle_missing_summary_and_legacy_ids():
    candidates = [
        {"start": 20.0, "end": 25.0, "text": "두 번째"},
        {"start": 5.0, "end": 10.0, "text": "첫 번째"},
    ]

    chapters = _build_candidate_chapters(candidates, {"chapters": None})

    assert [item["segment_id"] for item in candidates] == [
        "segment-0000",
        "segment-0001",
    ]
    assert [
        segment_id for chapter in chapters for segment_id in chapter["segment_ids"]
    ] == ["segment-0001", "segment-0000"]
    assert chapters[0]["title"] == "첫 번째"
    assert _build_candidate_chapters(candidates, {"chapters": 123}) == chapters
    assert _build_candidate_chapters(candidates, [{"bad": "root"}]) == chapters
    assert _build_candidate_chapters(candidates, "bad-root") == chapters
    assert _build_candidate_chapters(candidates, 7) == chapters


def test_timed_chapters_use_topic_boundaries_instead_of_equal_sizes():
    midpoints = [150.0, 50.0, 1.0, 104.9, 30.0, 105.0, 12.0, 90.0, 20.0, 49.9]
    candidates = [
        {
            "segment_id": f"segment-{index:02d}",
            "start": midpoint - 0.1,
            "end": midpoint + 0.1,
            "text": f"후보 {midpoint}",
        }
        for index, midpoint in enumerate(midpoints)
    ]
    summary = {
        "chapters": [
            {"title": "주제 C", "summary": "세 번째", "start": 120.0, "end": 140.0},
            {"title": "주제 A", "summary": "첫 번째", "start": 10.0, "end": 30.0},
            {"title": "주제 B", "summary": "두 번째", "start": 70.0, "end": 90.0},
        ]
    }

    chapters = _build_candidate_chapters(
        candidates,
        summary,
        max_segments_per_chapter=20,
        source_duration=180.0,
    )

    assert [chapter["title"] for chapter in chapters] == ["주제 A", "주제 B", "주제 C"]
    assert [len(chapter["segment_ids"]) for chapter in chapters] == [5, 3, 2]
    expected = [
        item["segment_id"]
        for item in sorted(candidates, key=lambda value: value["start"])
    ]
    flattened = [segment_id for chapter in chapters for segment_id in chapter["segment_ids"]]
    assert flattened == expected
    assert len(flattened) == len(set(flattened))
    assert "segment-01" in chapters[1]["segment_ids"]  # midpoint == 50 goes right
    assert "segment-05" in chapters[2]["segment_ids"]  # midpoint == 105 goes right


def test_invalid_timed_chapters_fall_back_to_balanced_assignment():
    candidates = [
        {
            "segment_id": f"segment-{index:02d}",
            "start": float(index * 10),
            "end": float(index * 10 + 5),
            "text": f"후보 {index}",
        }
        for index in range(6)
    ]
    invalid_chapters = [
        {"title": "오류", "summary": "누락", "start": 30.0},
        {"title": "오류", "summary": "문자열", "start": "30", "end": 60.0},
        {"title": "오류", "summary": "불리언", "start": True, "end": 60.0},
        {"title": "오류", "summary": "NaN", "start": float("nan"), "end": 60.0},
        {"title": "오류", "summary": "무한대", "start": 30.0, "end": float("inf")},
        {"title": "오류", "summary": "역전", "start": 60.0, "end": 30.0},
        {"title": "오류", "summary": "중첩 역행", "start": 10.0, "end": 20.0},
        {"title": "오류", "summary": "범위 초과", "start": 30.0, "end": 1_000_000.0},
    ]

    for invalid in invalid_chapters:
        chapters = _build_candidate_chapters(
            candidates,
            {
                "chapters": [
                    {"title": "정상", "summary": "앞부분", "start": 0.0, "end": 25.0},
                    invalid,
                ]
            },
            max_segments_per_chapter=20,
            source_duration=60.0,
        )
        assert [len(chapter["segment_ids"]) for chapter in chapters] == [3, 3]


def test_timed_chapter_cap_keeps_all_candidates_and_last_topic():
    candidates = [
        {
            "segment_id": f"segment-{index:02d}",
            "start": float(index * 10),
            "end": float(index * 10 + 8),
            "text": f"후보 {index}",
        }
        for index in range(20)
    ]
    summary = {
        "chapters": [
            {
                "title": f"주제 {index}",
                "summary": f"요약 {index}",
                "start": float(index * 10),
                "end": float(index * 10 + 8),
            }
            for index in range(20)
        ]
    }

    chapters = _build_candidate_chapters(
        candidates,
        summary,
        max_chapters=12,
        max_segments_per_chapter=20,
        source_duration=200.0,
    )

    flattened = [segment_id for chapter in chapters for segment_id in chapter["segment_ids"]]
    assert len(chapters) == 12
    assert flattened == [item["segment_id"] for item in candidates]
    assert "주제 0" in chapters[0]["title"]
    assert "주제 19" in chapters[-1]["title"]


def test_rerender_from_selection_uses_canonical_times_and_rebuilds_subtitles(
    tmp_path, monkeypatch
):
    output_dir, _ = _write_saved_review_job(tmp_path)
    captured = {}

    def fake_render(source, clips, output, subtitles, font_name, font_size, **_kwargs):
        captured["clips"] = clips
        captured["subtitles"] = subtitles.read_text(encoding="utf-8-sig")
        output.write_bytes(b"new-render")

    monkeypatch.setattr("app.services.live_edit_pipeline.render_preview", fake_render)

    result = LiveEditPipeline(tmp_path).rerender_from_selection(
        "review-job",
        ["segment-c", "segment-a", "segment-c"],
        feedback="첫 구간과 결론을 유지",
    )

    assert [clip["segment_id"] for clip in captured["clips"]] == [
        "segment-a",
        "segment-c",
    ]
    assert "첫 번째 후보" in captured["subtitles"]
    assert "세 번째 후보" in captured["subtitles"]
    assert "제외할 후보" not in captured["subtitles"]
    saved_plan = json.loads((output_dir / "edit_plan.json").read_text(encoding="utf-8"))
    assert saved_plan["selected_segment_ids"] == ["segment-a", "segment-c"]
    assert saved_plan["last_feedback"] == "첫 구간과 결론을 유지"
    assert saved_plan["revision"] == 1
    assert (output_dir / saved_plan["rendered_filename"]).read_bytes() == b"new-render"
    assert result["revision"] == 1


def test_rerender_rejects_unknown_segment_without_overwriting_existing_files(
    tmp_path, monkeypatch
):
    output_dir, original_plan = _write_saved_review_job(tmp_path)
    called = False

    def fake_render(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("app.services.live_edit_pipeline.render_preview", fake_render)

    try:
        LiveEditPipeline(tmp_path).rerender_from_selection(
            "review-job", ["missing-segment"]
        )
    except LiveEditPipelineError as exc:
        assert "존재하지 않는" in str(exc)
    else:
        raise AssertionError("unknown segment must be rejected")

    assert called is False
    assert json.loads((output_dir / "edit_plan.json").read_text(encoding="utf-8")) == original_plan
    assert (output_dir / "subtitles.srt").read_text(encoding="utf-8") == "기존 자막"
    assert (output_dir / "edited-preview.mp4").read_bytes() == b"old-render"

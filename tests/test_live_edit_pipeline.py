import json

import pytest

from app.services import live_edit_pipeline
from app.services.live_edit_pipeline import LiveEditPipeline, LiveEditPipelineError, _hardware_decoding_args, _time_seconds, _video_encoding_args
from app.services.live_youtube_service import LiveYouTubeError, load_prepared_transcript


def test_prepared_transcript_numeric_seconds_are_accepted():
    assert _time_seconds(1.25) == 1.25
    assert _time_seconds("1.25") == 1.25
    assert _time_seconds("01:02.5") == 62.5


def test_hardware_encoder_detection_uses_supported_gpu_encoder(monkeypatch):
    monkeypatch.setenv("AVE_VIDEO_ENCODER", "auto")
    live_edit_pipeline._preferred_video_encoder.cache_clear()
    monkeypatch.setattr(live_edit_pipeline, "_ffmpeg_binary", lambda: "ffmpeg")

    class Completed:
        returncode = 0
        stdout = " V....D h264_qsv Intel QSV H.264 encoder\n V....D h264_amf AMD AMF H.264 encoder\n V....D h264_nvenc NVIDIA NVENC H.264 encoder\n"
        stderr = ""

    monkeypatch.setattr(live_edit_pipeline.subprocess, "run", lambda *_args, **_kwargs: Completed())
    assert live_edit_pipeline._preferred_video_encoder() == "h264_nvenc"
    assert _video_encoding_args("h264_nvenc", preview=True)[:2] == ["-c:v", "h264_nvenc"]
    assert _hardware_decoding_args("h264_nvenc") == ["-hwaccel", "auto"]
    assert _hardware_decoding_args(None) == []
    live_edit_pipeline._preferred_video_encoder.cache_clear()


def test_hardware_encoder_priority_prefers_amd_over_intel(monkeypatch):
    monkeypatch.setenv("AVE_VIDEO_ENCODER", "auto")
    live_edit_pipeline._preferred_video_encoder.cache_clear()
    monkeypatch.setattr(live_edit_pipeline, "_ffmpeg_binary", lambda: "ffmpeg")

    class Completed:
        returncode = 0
        stdout = " V....D h264_qsv Intel QSV H.264 encoder\n V....D h264_amf AMD AMF H.264 encoder\n"
        stderr = ""

    monkeypatch.setattr(live_edit_pipeline.subprocess, "run", lambda *_args, **_kwargs: Completed())
    assert live_edit_pipeline._preferred_video_encoder() == "h264_amf"
    live_edit_pipeline._preferred_video_encoder.cache_clear()


def test_hardware_rendering_falls_back_to_cpu_when_gpu_encoder_fails(monkeypatch, tmp_path):
    attempts = []
    monkeypatch.setattr(live_edit_pipeline, "_preferred_video_encoder", lambda: "h264_nvenc")

    def render(*args):
        attempts.append(args[-1])
        if args[-1] == "h264_nvenc":
            raise LiveEditPipelineError("GPU를 초기화하지 못했습니다.")

    monkeypatch.setattr(live_edit_pipeline, "_render_exact", render)
    live_edit_pipeline.render_exact(tmp_path / "source.mp4", [], tmp_path / "output.mp4")

    assert attempts == ["h264_nvenc", None]


def test_prepared_transcript_uses_the_requested_language(tmp_path, monkeypatch):
    metadata_dir = tmp_path / "yt-edit" / "dQw4w9WgXcQ.metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "dQw4w9WgXcQ.en.captions-transcript.json").write_text(
        json.dumps({"segments": [{"text": "English"}]}), encoding="utf-8"
    )
    (metadata_dir / "dQw4w9WgXcQ.ko.captions-transcript.json").write_text(
        json.dumps({"segments": [{"text": "한국어"}]}), encoding="utf-8"
    )
    monkeypatch.setattr("app.services.live_youtube_service.get_media_root", lambda: tmp_path)

    assert load_prepared_transcript("dQw4w9WgXcQ", "captions", "ko") == [{"text": "한국어"}]
    with pytest.raises(LiveYouTubeError, match="en subtitles"):
        load_prepared_transcript("dQw4w9WgXcQ", "subtitles", "en")


def test_selection_render_uses_and_removes_temporary_srt(tmp_path, monkeypatch):
    job_id = "review-job"
    output_dir = tmp_path / "yt-edit" / job_id
    output_dir.mkdir(parents=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    plan = {
        "source_video_path": str(source),
        "render_mode": "preview",
        "subtitle_font_name": "Malgun Gothic",
        "subtitle_font_size": 18,
        "candidates": [{"segment_id": "segment-a", "start": 10.0, "end": 20.0, "text": "후보"}],
        "recommended_segment_ids": ["segment-a"],
        "selected_segment_ids": ["segment-a"],
        "clips": [{"start": 9.6, "end": 20.6}],
    }
    plan["script_segments"] = [{"start": 10.0, "end": 20.0, "text": "후보"}]

    def fake_render(_source, _clips, output, subtitles, *_args, **_kwargs):
        assert subtitles.is_file()
        output.write_bytes(b"rendered")

    monkeypatch.setattr("app.services.live_edit_pipeline.render_preview", fake_render)
    result = LiveEditPipeline(tmp_path).rerender_from_selection(job_id, ["segment-a"], plan=plan)

    assert (output_dir / result["rendered_filename"]).read_bytes() == b"rendered"
    assert not (output_dir / f"{job_id}.render-input.srt").exists()


def test_selection_rejects_unknown_segment(tmp_path):
    try:
        LiveEditPipeline(tmp_path).rerender_from_selection(
            "bad-selection", ["missing"],
            plan={"candidates": [{"segment_id": "chapter-00", "start": 0, "end": 10}]},
        )
    except LiveEditPipelineError as exc:
        assert "존재하지 않는" in str(exc)
    else:
        raise AssertionError("unknown segment must be rejected")


def test_review_exposes_chapter_section_hierarchy(tmp_path):
    review = LiveEditPipeline(tmp_path).get_segment_review("review-job", {
        "target_seconds": 60,
        "selected_segment_ids": ["chapter-00-section-00"],
        "recommended_segment_ids": ["chapter-00-section-00"],
        "candidates": [{"segment_id": "chapter-00-section-00", "chapter_id": "chapter-00", "section_id": "chapter-00-section-00", "start": 0, "end": 4, "text": "섹션", "llm_score": 900}],
        "chapters": [{"chapter_id": "chapter-00", "summary": "주제 요약", "llm_score": 812, "start": 0, "end": 4, "sections": [{"section_id": "chapter-00-section-00", "start": 0, "end": 4, "segment_ids": ["chapter-00-section-00"]}]}],
    })

    assert review["chapters"][0]["sections"][0]["segment_ids"] == ["chapter-00-section-00"]
    assert "segments" not in review
    assert review["chapters"][0]["llm_score"] == 812
    assert review["chapters"][0]["sections"][0]["llm_score"] == 900
    assert "final_score" not in review["chapters"][0]["sections"][0]

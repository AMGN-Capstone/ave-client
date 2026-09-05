"""2단계에서 준비한 YouTube VOD 자료를 재사용하는 편집 파이프라인."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.config import get_media_root
from app.services.toolchain import ToolchainError, ffmpeg as get_ffmpeg
from app.services.server_media_service import ServerMediaError, TranscriptionCancelledError, acknowledge_transcription_result, transcribe_uploaded_audio, upload_audio_for_transcription
from app.services.llm_analysis_service import LLMAnalysisError, LLMAnalysisService
from app.services.youtube_importer import YouTubeImporter
from app.services.live_youtube_service import LiveYouTubeError, extract_video_id, load_prepared_transcript


EDIT_GENRES = {"ai_news", "stock", "game"}


class LiveEditPipelineError(RuntimeError):
    pass


class LiveEditCancelled(LiveEditPipelineError):
    pass


def _time_seconds(value: str | int | float) -> float:
    """Accept prepared JSON seconds as well as legacy WebVTT clock text."""

    if isinstance(value, bool):
        raise ValueError("시간 값은 숫자여야 합니다.")
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().replace(",", ".").split(":")
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes) * 60 + float(seconds)
    if len(parts) != 3:
        raise ValueError("시간 값 형식이 올바르지 않습니다.")
    hours, minutes, seconds = parts
    return float(hours) * 3600 + float(minutes) * 60 + float(seconds)


def _ensure_candidate_ids(candidates: list[dict[str, Any]]) -> None:
    """Give legacy and current candidates unique deterministic IDs in place."""

    seen: set[str] = set()
    for index, item in enumerate(candidates):
        candidate_id = str(item.get("segment_id") or "").strip()
        if not candidate_id or candidate_id in seen:
            candidate_id = f"segment-{index:04d}"
            suffix = 1
            while candidate_id in seen:
                candidate_id = f"segment-{index:04d}-{suffix}"
                suffix += 1
            item["segment_id"] = candidate_id
        seen.add(candidate_id)


def _select_clips(sections: list[dict[str, Any]], target_seconds: int) -> list[dict[str, Any]]:
    """섹션 LLM 점수와 정확한 섹션 경계만으로 배낭 선택을 수행한다."""

    candidates = [item for item in sections if item["end"] - item["start"] >= 5.0]
    unit = 2
    target = target_seconds * unit
    upper = (target_seconds + 30) * unit
    states: dict[int, tuple[float, list[dict[str, Any]]]] = {0: (0.0, [])}
    for item in candidates:
        duration = max(1, round((item["end"] - item["start"]) * unit))
        value = float(item.get("llm_score", 0.0))
        snapshot = list(states.items())
        for used, (score, selected) in snapshot:
            new_used = used + duration
            if new_used > upper:
                continue
            new_score = score + value
            if new_used not in states or new_score > states[new_used][0]:
                states[new_used] = (new_score, selected + [item])
    valid = [(abs(used - target), -score, selected) for used, (score, selected) in states.items() if used >= max(1, target - 30 * unit)]
    if not valid:
        valid = [(abs(used - target), -score, selected) for used, (score, selected) in states.items()]
    valid.sort(key=lambda value: (value[0], value[1]))
    return sorted(valid[0][2], key=lambda item: item["start"])


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_selected_subtitles(
    segments: list[dict[str, Any]],
    clips: list[dict[str, Any]],
    output: Path,
) -> int:
    """Write cleaned source captions on the concatenated edit timeline."""

    entries: list[tuple[float, float, str]] = []
    timeline_offset = 0.0
    for clip in clips:
        clip_start = float(clip["start"])
        clip_end = float(clip["end"])
        for segment in segments:
            start = max(clip_start, float(segment["start"]))
            end = min(clip_end, float(segment["end"]))
            text = re.sub(r"\s+", " ", str(segment.get("text", ""))).strip()
            if end - start < 0.08 or not text:
                continue
            output_start = timeline_offset + start - clip_start
            output_end = timeline_offset + end - clip_start
            if entries and text == entries[-1][2] and output_start <= entries[-1][1] + 0.2:
                entries[-1] = (entries[-1][0], max(entries[-1][1], output_end), text)
            else:
                entries.append((output_start, output_end, text))
        timeline_offset += max(0.0, clip_end - clip_start)

    lines: list[str] = []
    for index, (start, end, value) in enumerate(entries, start=1):
        wrapped = "\n".join(textwrap.wrap(value, width=38, break_long_words=False, break_on_hyphens=False))
        lines.extend([str(index), f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}", wrapped, ""])
    output.write_text("\n".join(lines), encoding="utf-8")
    return len(entries)


def _run_ffmpeg(command: list[str]) -> None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise LiveEditPipelineError("ffmpeg를 실행할 수 없습니다. PATH에 ffmpeg를 추가하세요.") from exc
    if completed.returncode != 0:
        raise LiveEditPipelineError(completed.stderr[-3000:] or "ffmpeg 편집에 실패했습니다.")


def _ffmpeg_binary() -> str:
    try:
        return str(get_ffmpeg())
    except ToolchainError as exc:
        raise LiveEditPipelineError(str(exc)) from exc


# 기본 선택 순서는 실제 GPU 우선순위와 같다. NVIDIA > AMD > Intel.
_HARDWARE_VIDEO_ENCODERS = ("h264_nvenc", "h264_amf", "h264_qsv")


@lru_cache(maxsize=1)
def _preferred_video_encoder() -> str | None:
    """지원되는 GPU H.264 인코더를 찾고, 없으면 CPU 인코더를 사용한다."""

    configured = os.getenv("AVE_VIDEO_ENCODER", "auto").strip().lower()
    if configured == "cpu":
        return None
    candidates = _HARDWARE_VIDEO_ENCODERS if configured in {"", "auto"} else (configured,)
    try:
        completed = subprocess.run([_ffmpeg_binary(), "-hide_banner", "-encoders"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    available = completed.stdout + completed.stderr
    return next((encoder for encoder in candidates if encoder in _HARDWARE_VIDEO_ENCODERS and encoder in available), None)


def _video_encoding_args(encoder: str | None, *, preview: bool) -> list[str]:
    """인코더별 품질·속도 옵션. 지원하지 않으면 libx264로 폴백한다."""

    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p1" if preview else "p4", "-tune", "hq", "-cq", "23" if preview else "20", "-b:v", "0"]
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", "veryfast" if preview else "fast", "-global_quality", "23" if preview else "20"]
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "speed" if preview else "balanced", "-qp_i", "23" if preview else "20", "-qp_p", "23" if preview else "20"]
    return ["-c:v", "libx264", "-preset", "veryfast" if preview else "fast", "-crf", "23" if preview else "20"]


def _hardware_decoding_args(encoder: str | None) -> list[str]:
    """GPU 렌더링에서만 FFmpeg가 적합한 하드웨어 디코더를 선택하게 한다.

    출력 포맷을 GPU 프레임으로 강제하지 않아 자막·trim 등 CPU 필터와의 호환성을
    지킨다. 드라이버 또는 필터가 이를 지원하지 않으면 상위 렌더러가 CPU 작업을
    처음부터 다시 수행한다.
    """

    return ["-hwaccel", "auto"] if encoder else []


def _burn_subtitles(
    source: Path,
    subtitles: Path,
    output: Path,
    font_name: str = "Malgun Gothic",
    font_size: int = 18,
    encoder: str | None = None,
) -> None:
    ffmpeg = _ffmpeg_binary()
    if not subtitles.is_file():
        raise LiveEditPipelineError(f"FFmpeg 자막 입력 파일을 찾을 수 없습니다: {subtitles}")
    # Windows filtergraph에서는 드라이브 구분자와 경로 구분자 모두 FFmpeg
    # 이스케이프가 필요하다. libass가 실제 Windows 경로를 받도록 backslash를
    # 두 번 이스케이프한다.
    filter_path = str(subtitles.resolve()).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
    safe_font_name = re.sub(r"[\\:'&,]", "", str(font_name)).strip() or "Malgun Gothic"
    safe_font_size = max(8, min(64, int(font_size)))
    subtitle_filter = (
        f"subtitles=filename='{filter_path}':"
        f"force_style='FontName={safe_font_name},FontSize={safe_font_size},"
        "Outline=2,Shadow=1,MarginV=36'"
    )
    try:
        _run_ffmpeg([
            ffmpeg, "-y", *_hardware_decoding_args(encoder), "-i", str(source), "-vf", subtitle_filter,
            *_video_encoding_args(encoder, preview=False),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output),
        ])
    except LiveEditPipelineError as exc:
        raise LiveEditPipelineError(
            f"FFmpeg 자막 합성에 실패했습니다 (자막 파일: {subtitles.resolve()}, {subtitles.stat().st_size} bytes): {exc}"
        ) from exc


def render_preview(
    source: Path,
    clips: list[dict[str, Any]],
    output: Path,
    subtitles: Path | None = None,
    font_name: str = "Malgun Gothic",
    font_size: int = 18,
    progress_callback: Callable[[float], None] | None = None,
) -> None:
    encoder = _preferred_video_encoder()
    try:
        _render_preview(source, clips, output, subtitles, font_name, font_size, progress_callback, encoder)
    except LiveEditPipelineError:
        if encoder is None:
            raise
        # 컴파일된 인코더가 드라이버·장치 제약으로 실행되지 않으면 같은 작업을
        # CPU로 다시 실행해 결과 생성 자체가 실패하지 않게 한다.
        _render_preview(source, clips, output, subtitles, font_name, font_size, progress_callback, None)


def _render_preview(
    source: Path,
    clips: list[dict[str, Any]],
    output: Path,
    subtitles: Path | None = None,
    font_name: str = "Malgun Gothic",
    font_size: int = 18,
    progress_callback: Callable[[float], None] | None = None,
    encoder: str | None = None,
) -> None:
    ffmpeg = _ffmpeg_binary()
    with tempfile.TemporaryDirectory(prefix="live-edit-") as temp_name:
        temp = Path(temp_name)
        files = []
        total_duration = sum(max(0.0, float(clip["end"]) - float(clip["start"])) for clip in clips)
        completed_duration = 0.0
        for index, clip in enumerate(clips):
            segment = temp / f"segment-{index:04d}.mp4"
            _run_ffmpeg([
                # Stream-copy seeking starts on a nearby keyframe. Each clip can
                # then be slightly longer/shorter than its requested duration,
                # causing subtitle timestamps to drift further on every join.
                # Re-encode the preview clips from an accurate post-input seek
                # so their concatenated timeline matches write_selected_subtitles.
                ffmpeg, "-y", *_hardware_decoding_args(encoder), "-i", str(source), "-ss", str(clip["start"]),
                "-t", str(clip["end"] - clip["start"]), "-map", "0:v:0", "-map", "0:a:0?",
                *_video_encoding_args(encoder, preview=True),
                "-c:a", "aac", "-b:a", "160k", "-avoid_negative_ts", "make_zero", str(segment),
            ])
            files.append(segment)
            completed_duration += max(0.0, float(clip["end"]) - float(clip["start"]))
            if progress_callback:
                progress_callback(0.75 * completed_duration / max(0.001, total_duration))
        concat = temp / "concat.txt"
        concat.write_text("\n".join(f"file '{path.as_posix()}'" for path in files), encoding="utf-8")
        joined = temp / "joined.mp4"
        _run_ffmpeg([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(joined)])
        if progress_callback:
            progress_callback(0.82)
        if subtitles and subtitles.exists() and subtitles.stat().st_size > 0:
            _burn_subtitles(joined, subtitles, output, font_name, font_size, encoder)
        else:
            shutil.copyfile(joined, output)
        if progress_callback:
            progress_callback(1.0)


def render_exact(
    source: Path,
    clips: list[dict[str, Any]],
    output: Path,
    subtitles: Path | None = None,
    font_name: str = "Malgun Gothic",
    font_size: int = 18,
    progress_callback: Callable[[float], None] | None = None,
) -> None:
    encoder = _preferred_video_encoder()
    try:
        _render_exact(source, clips, output, subtitles, font_name, font_size, progress_callback, encoder)
    except LiveEditPipelineError:
        if encoder is None:
            raise
        _render_exact(source, clips, output, subtitles, font_name, font_size, progress_callback, None)


def _render_exact(
    source: Path,
    clips: list[dict[str, Any]],
    output: Path,
    subtitles: Path | None = None,
    font_name: str = "Malgun Gothic",
    font_size: int = 18,
    progress_callback: Callable[[float], None] | None = None,
    encoder: str | None = None,
) -> None:
    ffmpeg = _ffmpeg_binary()
    with tempfile.TemporaryDirectory(prefix="live-edit-exact-") as temp_name:
        joined = Path(temp_name) / "joined.mp4"
        video_parts = []
        audio_parts = []
        for index, clip in enumerate(clips):
            start, end = clip["start"], clip["end"]
            video_parts.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]")
            audio_parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]")
        concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(len(clips)))
        filter_graph = ";".join(video_parts + audio_parts) + f";{concat_inputs}concat=n={len(clips)}:v=1:a=1[v][a]"
        _run_ffmpeg([
            ffmpeg, "-y", *_hardware_decoding_args(encoder), "-i", str(source), "-filter_complex", filter_graph,
            "-map", "[v]", "-map", "[a]", *_video_encoding_args(encoder, preview=False),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(joined),
        ])
        if progress_callback:
            progress_callback(0.82)
        if subtitles and subtitles.exists() and subtitles.stat().st_size > 0:
            _burn_subtitles(joined, subtitles, output, font_name, font_size, encoder)
        else:
            shutil.copyfile(joined, output)
        if progress_callback:
            progress_callback(1.0)


class LiveEditPipeline:
    def __init__(self, media_root: Path | None = None):
        self.media_root = (media_root or get_media_root()).resolve()

    def run(
        self,
        *,
        job_id: str | None = None,
        vod_url: str,
        genre: str = "ai_news",
        llm_provider: str = "deepseek",
        target_seconds: int = 600,
        transcription_source: str = "youtube_caption",
        transcript_language: str | None = None,
        stt_language: str = "ko",
        stt_initial_prompt: str | None = None,
        stt_hotwords: str | None = None,
        stt_speed: float = 1.0,
        subtitle_font_name: str = "Malgun Gothic",
        subtitle_font_size: int = 18,
        render_mode: str = "preview",
        defer_render: bool = True,
        progress_callback: Callable[[int, str], None] | None = None,
        cancel_callback: Callable[[], None] | None = None,
        whisper_progress_callback: Callable[[int, str], None] | None = None,
        whisper_preparing_callback: Callable[[], None] | None = None,
        whisper_job_started_callback: Callable[[str], None] | None = None,
        server_access_token: str | None = None,
        server_job_id: str | None = None,
    ) -> dict[str, Any]:
        def report(progress: int, message: str) -> None:
            if progress_callback:
                progress_callback(progress, message)

        if target_seconds < 60 or target_seconds > 3600:
            raise LiveEditPipelineError("target_seconds는 60초에서 3600초 사이여야 합니다.")
        if genre not in EDIT_GENRES:
            raise LiveEditPipelineError("genre는 ai_news, stock 또는 game이어야 합니다.")
        if render_mode not in {"preview", "exact"}:
            raise LiveEditPipelineError("render_mode는 preview 또는 exact여야 합니다.")

        job_id = job_id or uuid4().hex
        if Path(job_id).name != job_id:
            raise LiveEditPipelineError("잘못된 편집 작업 ID입니다.")
        output_dir = self.media_root / "yt-edit" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        report(5, "1·2단계에서 준비한 원본과 스크립트를 확인하는 중입니다.")
        importer = YouTubeImporter(self.media_root)
        imported = importer.find_complete_cached_import(vod_url, job_id=f"edit-{job_id}")
        if imported is None:
            raise LiveEditPipelineError("1·2단계에서 준비한 원본 영상과 자막/캡션을 찾지 못했습니다.")
        report(18, "1·2단계에서 준비한 원본 영상과 자막을 재사용합니다.")
        source = Path(imported.get("video_path", ""))
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        if not source.exists():
            raise LiveEditPipelineError("yt-dlp가 원본 영상을 저장하지 못했습니다.")

        try:
            video_id = extract_video_id(vod_url)
        except LiveYouTubeError as exc:
            raise LiveEditPipelineError(str(exc)) from exc
        source_kind = {"youtube_caption": "captions", "youtube_subtitle": "subtitles"}.get(transcription_source)
        raw_segments: list[dict[str, Any]] = []
        if source_kind:
            if not transcript_language:
                raise LiveEditPipelineError("2단계에서 선택한 스크립트 언어를 지정하세요.")
            try:
                parsed_rows = load_prepared_transcript(video_id, source_kind, transcript_language)
                raw_segments = [
                    {"start": _time_seconds(row["start"]), "end": _time_seconds(row["end"]), "text": row["text"]}
                    for row in parsed_rows if isinstance(row, dict) and row.get("text")
                ]
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise LiveEditPipelineError("2단계 스크립트 파일 형식이 올바르지 않습니다.") from exc
        if transcription_source == "whisper_api":
            if whisper_preparing_callback:
                whisper_preparing_callback()
            report(22, "음원을 AVE 서버에 올리는 중입니다.")
            try:
                uploaded_audio = upload_audio_for_transcription(source, server_access_token or "")
                report(25, "Whisper API로 음성을 전사하는 중입니다. 처음 요청은 모델 준비로 오래 걸릴 수 있습니다.")
                whisper_remote_job_id: str | None = None

                def record_whisper_job(remote_job_id: str) -> None:
                    nonlocal whisper_remote_job_id
                    whisper_remote_job_id = remote_job_id
                    if whisper_job_started_callback:
                        whisper_job_started_callback(remote_job_id)

                transcription_result = transcribe_uploaded_audio(
                    uploaded_audio.file_id,
                    server_access_token or "",
                    client_job_id=job_id,
                    server_job_id=server_job_id,
                    language=stt_language,
                    initial_prompt=stt_initial_prompt,
                    hotwords=stt_hotwords,
                    speed=stt_speed,
                    progress_callback=whisper_progress_callback,
                    job_started_callback=record_whisper_job,
                )
                raw_segments = [
                    {"start": float(segment["start"]), "end": float(segment["end"]), "text": str(segment["text"])}
                    for segment in transcription_result["segments"]
                    if isinstance(segment, dict)
                ]
                (output_dir / f"{job_id}.whisper-transcript.json").write_text(
                    json.dumps({"segments": raw_segments}, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                if whisper_remote_job_id:
                    try:
                        acknowledge_transcription_result(whisper_remote_job_id, server_access_token or "")
                    except ServerMediaError:
                        pass
            except TranscriptionCancelledError as exc:
                raise LiveEditCancelled(str(exc)) from exc
            except ServerMediaError as exc:
                raise LiveEditPipelineError(str(exc)) from exc
        if not raw_segments:
            raise LiveEditPipelineError("자막 파일은 있지만 시간표시 문장을 읽지 못했습니다.")

        raw_segments = [{**segment, "id": index} for index, segment in enumerate(raw_segments)]
        report(22, f"자막 {len(raw_segments):,}개 구간을 확인했습니다.")
        analysis_service = LLMAnalysisService(provider=llm_provider, server_access_token=server_access_token)
        try:
            structure = analysis_service.structure_transcript(
                raw_segments,
                progress_callback=lambda done, total, label: report(25 + int(25 * done / max(1, total)), f"LLM {label} ({done}/{total})"),
                cancel_callback=cancel_callback,
            )
        except LLMAnalysisError as exc:
            raise LiveEditPipelineError(f"LLM 챕터·섹션 분할에 실패했습니다: {exc}") from exc
        subtitle_by_id = {int(item["id"]): item for item in raw_segments}
        candidates: list[dict[str, Any]] = []
        chapters: list[dict[str, Any]] = []
        for index, chapter in enumerate(structure["chapters"]):
            first, last = subtitle_by_id[chapter["start_id"]], subtitle_by_id[chapter["end_id"]]
            chapter_id = f"chapter-{index:02d}"
            chapter_sections: list[dict[str, Any]] = []
            for section_index, section in enumerate(section for section in structure["sections"] if section["chapter_index"] == index):
                section_first = subtitle_by_id[section["start_id"]]
                section_last = subtitle_by_id[section["end_id"]]
                section_id = f"{chapter_id}-section-{section_index:02d}"
                chapter_sections.append({
                    "section_id": section_id,
                    "start": float(section_first["start"]),
                    "end": float(section_last["end"]),
                    "segment_ids": [section_id],
                })
                text = " ".join(str(item["text"]) for item in raw_segments if section["start_id"] <= int(item["id"]) <= section["end_id"])
                candidates.append({
                    "segment_id": section_id,
                    "start": float(section_first["start"]),
                    "end": float(section_last["end"]),
                    "text": text,
                    "chapter_id": chapter_id,
                    "section_id": section_id,
                    "chapter_summary": chapter["summary"],
                })
            chapters.append({"chapter_id": chapter_id, "summary": chapter["summary"], "llm_score": float(chapter["score"]), "start": float(first["start"]), "end": float(last["end"]), "sections": chapter_sections})
        report(55, "LLM이 전체 스크립트를 챕터와 섹션으로 분할했습니다.")
        scored = analysis_service.score_sections(
            candidates,
            genre=genre,
            progress_callback=lambda done, total, label: report(55 + int(25 * done / max(1, total)), f"LLM {label} ({done}/{total})"),
            cancel_callback=cancel_callback,
        )
        report(80, f"LLM이 섹션 {len(scored):,}개의 중요도를 평가했습니다.")
        # Stable IDs let the browser send a compact, auditable selection back
        # without trusting client-provided timestamps or text.
        _ensure_candidate_ids(scored)
        duration = float(imported.get("duration") or 0.0)
        selected = _select_clips(scored, target_seconds)
        recommended_segment_ids = [str(item["segment_id"]) for item in selected]
        clips = selected
        if not clips:
            raise LiveEditPipelineError("편집할 하이라이트 구간을 선택하지 못했습니다.")

        report(88, f"최종 하이라이트 {len(clips):,}개 구간을 선택했습니다.")
        plan = {
            "vod_url": vod_url,
            "genre": genre,
            "llm_provider": llm_provider,
            "transcription_source": transcription_source,
            "target_seconds": target_seconds,
            "source_video_path": str(source.resolve()),
            "render_mode": render_mode,
            "subtitle_font_name": subtitle_font_name,
            "subtitle_font_size": subtitle_font_size,
            "rendered_filename": f"edited-{render_mode}.mp4",
            "source_duration_seconds": duration,
            "candidates": scored,
            "chapters": chapters,
            "recommended_segment_ids": recommended_segment_ids,
            "selected_segment_ids": recommended_segment_ids,
            "clips": clips,
        }

        base_result = {
            "job_id": job_id,
            "vod_url": vod_url,
            "vod_video_id": imported.get("job_id"),
            "genre": genre,
            "llm_provider": llm_provider,
            "source_video_path": str(source),
            "transcription_source": transcription_source,
            "subtitles_burned_in": False,
            "rendered_video_path": None,
            "render_mode": render_mode,
            "subtitle_font_name": subtitle_font_name,
            "subtitle_font_size": subtitle_font_size,
            "target_seconds": target_seconds,
            "selected_duration_seconds": round(sum(item["end"] - item["start"] for item in clips), 3),
            # 분석 전사문은 활성 작업 메모리에만 보관하며, 선택 렌더링 뒤
            # SQLite와 서버 이력에는 LocalJobStore가 최소 계획만 남긴다.
            "analysis_plan": {**plan, "script_segments": raw_segments},
            "recommended_segment_ids": recommended_segment_ids,
            "clips": clips,
            "awaiting_selection": defer_render,
        }
        if defer_render:
            report(90, "AI 분석이 완료되었습니다. 웹에서 원하는 구간을 선택하세요.")
            return base_result

        rendered = output_dir / f"{job_id}.edited-{render_mode}.mp4"
        with tempfile.TemporaryDirectory(prefix="ave-srt-") as temporary:
            subtitles = Path(temporary) / "render.srt"
            subtitle_count = write_selected_subtitles(raw_segments, clips, subtitles)
            if render_mode == "preview":
                render_preview(source, clips, rendered, subtitles, subtitle_font_name, subtitle_font_size)
            else:
                render_exact(source, clips, rendered, subtitles, subtitle_font_name, subtitle_font_size)
        report(100, "AI 영상 편집이 완료되었습니다.")
        return {
            **base_result,
            "subtitles_burned_in": subtitle_count > 0,
            "rendered_filename": rendered.name,
            "rendered_video_path": str(rendered.resolve()),
            "awaiting_selection": False,
        }

    def get_segment_review(self, job_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Return browser-safe candidates and the current user selection."""

        candidates = plan.get("candidates") or []
        if not candidates:
            raise LiveEditPipelineError("검토할 AI 후보 구간 파일을 찾을 수 없습니다.")
        _ensure_candidate_ids(candidates)

        selected_ids = [str(value) for value in plan.get("selected_segment_ids") or []]
        recommended_ids = [
            str(value)
            for value in plan.get("recommended_segment_ids") or selected_ids
        ]
        selected_set = set(selected_ids)
        candidate_by_id = {str(item["segment_id"]): item for item in candidates}
        chapters = []
        for chapter in plan.get("chapters") or []:
            sections = []
            for section in chapter.get("sections") or []:
                candidate = candidate_by_id.get(str(section.get("section_id")))
                if not candidate:
                    continue
                sections.append({
                    **section,
                    "text": candidate.get("text", ""),
                    "llm_score": candidate.get("llm_score"),
                    "selected": str(candidate["segment_id"]) in selected_set,
                })
            chapters.append({**chapter, "sections": sections})
        clips = plan.get("clips") or []
        render_mode = str(plan.get("render_mode", "preview"))
        return {
            "job_id": job_id,
            "genre": plan.get("genre", "ai_news"),
            "target_seconds": int(plan.get("target_seconds") or 0),
            "selected_segment_ids": selected_ids,
            "recommended_segment_ids": recommended_ids,
            "chapters": chapters,
            "selected_duration_seconds": round(
                sum(float(item["end"]) - float(item["start"]) for item in clips),
                3,
            ),
            "revision": int(plan.get("revision") or 0),
            "source_video_url": f"/api/youtube/edit/{job_id}/media/source",
            "rendered_video_url": f"/api/youtube/edit/{job_id}/media/rendered",
            "render_mode": render_mode,
        }

    def rerender_from_selection(
        self,
        job_id: str,
        segment_ids: list[str],
        *,
        plan: dict[str, Any],
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        """Render an existing analysis again from user-selected candidates."""

        def report(progress: int, message: str) -> None:
            if progress_callback:
                progress_callback(progress, message)

        if not job_id or Path(job_id).name != job_id:
            raise LiveEditPipelineError("잘못된 편집 작업 ID입니다.")
        output_dir = self.media_root / "yt-edit" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        candidates = plan.get("candidates") or []
        if not candidates:
            raise LiveEditPipelineError(
                "이 작업에는 선택 가능한 후보 구간이 없습니다. AI 분석을 다시 실행하세요."
            )

        _ensure_candidate_ids(candidates)
        requested_ids = list(dict.fromkeys(str(value) for value in segment_ids))
        if not requested_ids:
            raise LiveEditPipelineError("한 개 이상의 구간을 선택하세요.")
        by_id = {str(item.get("segment_id")): item for item in candidates}
        unknown = [value for value in requested_ids if value not in by_id]
        if unknown:
            raise LiveEditPipelineError("존재하지 않는 후보 구간이 포함되어 있습니다.")

        source = Path(str(plan.get("source_video_path", "")))
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        if not source.exists():
            raise LiveEditPipelineError("다시 편집할 원본 영상을 찾을 수 없습니다.")

        report(0, "선택한 구간을 시간순으로 정리하는 중입니다.")
        selected = sorted(
            (by_id[value] for value in requested_ids),
            key=lambda item: float(item["start"]),
        )
        canonical_ids = [str(item["segment_id"]) for item in selected]
        clips = selected
        if not clips:
            raise LiveEditPipelineError("선택한 구간에서 유효한 편집 범위를 만들지 못했습니다.")

        raw_segments = plan.get("script_segments")
        if not isinstance(raw_segments, list):
            raise LiveEditPipelineError("메모리의 원본 스크립트를 찾을 수 없습니다.")

        report(0, "선택한 구간에 맞춰 자막 시간축을 다시 만드는 중입니다.")
        render_mode = str(plan.get("render_mode", "preview"))
        font_name = str(plan.get("subtitle_font_name", "Malgun Gothic"))
        font_size = int(plan.get("subtitle_font_size", 18))
        revision = int(plan.get("revision") or 0) + 1
        output = output_dir / f"{job_id}.edited-{render_mode}.mp4"
        pending_output = output_dir / f"{job_id}.edited-{render_mode}.pending.mp4"
        report(0, f"사용자가 선택한 {len(canonical_ids):,}개 구간을 렌더링하는 중입니다.")
        def report_render_progress(fraction: float) -> None:
            percent = max(0, min(98, int(round(float(fraction) * 98))))
            report(percent, f"영상 렌더링 진행률 {percent}%")

        pending_subtitles = output_dir / f"{job_id}.render-input.srt"
        try:
            subtitle_count = write_selected_subtitles(raw_segments, clips, pending_subtitles)
            if render_mode == "preview":
                render_preview(source, clips, pending_output, pending_subtitles, font_name, font_size, progress_callback=report_render_progress)
            elif render_mode == "exact":
                render_exact(source, clips, pending_output, pending_subtitles, font_name, font_size, progress_callback=report_render_progress)
            else:
                raise LiveEditPipelineError("저장된 렌더링 방식이 올바르지 않습니다.")
        except Exception:
            pending_output.unlink(missing_ok=True)
            raise
        finally:
            pending_subtitles.unlink(missing_ok=True)

        os.replace(pending_output, output)
        plan.update(
            {
                "clips": clips,
                "selected_segment_ids": canonical_ids,
                "subtitle_count": subtitle_count,
                "revision": revision,
                "rendered_filename": output.name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        report(100, "선택한 구간으로 영상을 다시 만들었습니다.")
        return {
            **self.get_segment_review(job_id, plan),
            "rendered_video_path": str(output.resolve()),
            "rendered_filename": output.name,
            "message": "선택한 구간으로 영상을 다시 생성했습니다.",
        }

"""YouTube VOD + replay-chat driven longform editing pipeline."""

from __future__ import annotations

import html
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.config import get_media_root
from app.services.gemini_agents import GeminiAgents
from app.services.youtube_importer import YouTubeImporter


EDIT_GENRES = {"ai_news", "stock", "game"}


class LiveEditPipelineError(RuntimeError):
    pass


_VTT_TIME = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?|\d{2}:\d{2}\.\d{1,3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?|\d{2}:\d{2}\.\d{1,3})"
)


def _time_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes) * 60 + float(seconds)
    hours, minutes, seconds = parts
    return float(hours) * 3600 + float(minutes) * 60 + float(seconds)


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def parse_vtt(path: Path) -> list[dict[str, Any]]:
    """Parse timestamped VTT cues into the common transcript format."""

    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    segments: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        match = _VTT_TIME.search(lines[index])
        if not match:
            index += 1
            continue
        text_lines: list[str] = []
        index += 1
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        text = re.sub(r"<[^>]+>", "", html.unescape(" ".join(text_lines))).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            start = _time_seconds(match.group("start"))
            end = _time_seconds(match.group("end"))
            if end > start:
                segments.append({"start": start, "end": end, "text": text})
        index += 1
    return segments


def _load_replay_messages(archive_path: Path, actual_start_time: str | None, delay_seconds: float) -> list[dict[str, Any]]:
    if not archive_path.exists():
        return []
    start = None
    if actual_start_time:
        try:
            start = datetime.fromisoformat(actual_start_time.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            start = None

    records: list[dict[str, Any]] = []
    for line in archive_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        elapsed = item.get("elapsed_seconds")
        if elapsed is None and start and item.get("time"):
            try:
                published = datetime.fromisoformat(str(item["time"]).replace("Z", "+00:00")).astimezone(timezone.utc)
                elapsed = (published - start).total_seconds() - delay_seconds
            except ValueError:
                elapsed = None
        try:
            elapsed = float(elapsed)
        except (TypeError, ValueError):
            continue
        if elapsed >= 0:
            records.append({**item, "elapsed_seconds": elapsed})
    return records


def score_chat_density(
    segments: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    bucket_seconds: int = 30,
) -> list[dict[str, Any]]:
    """Attach robust 0-1000 chat-density scores to transcript clusters."""

    buckets: dict[int, float] = {}
    for item in messages:
        bucket = int(float(item["elapsed_seconds"]) // bucket_seconds)
        weight = 1.5 if item.get("super_chat") else 1.0
        buckets[bucket] = buckets.get(bucket, 0.0) + weight
    rates = [value * 60 / bucket_seconds for value in buckets.values()]
    scale = max(_percentile(rates, 0.9), 1.0)

    result = []
    for item in segments:
        first = int(max(0.0, float(item["start"])) // bucket_seconds)
        last = int(max(0.0, float(item["end"]) - 0.001) // bucket_seconds)
        weighted_count = sum(buckets.get(index, 0.0) for index in range(first, last + 1))
        duration = max(0.5, float(item["end"]) - float(item["start"]))
        density = weighted_count * 60.0 / duration
        score = min(1000.0, 1000.0 * math.log1p(density) / math.log1p(scale * 2.0)) if density else 0.0
        result.append({
            **item,
            "chat_count": int(round(weighted_count)),
            "chat_density": round(density, 3),
            "chat_score": round(score, 3),
        })
    return result


def _cluster_transcript(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for segment in segments:
        if not current:
            current = [segment]
            continue
        duration = segment["end"] - current[0]["start"]
        gap = segment["start"] - current[-1]["end"]
        if gap > 3.0 or duration > 22.0:
            clusters.append({"start": current[0]["start"], "end": current[-1]["end"], "text": " ".join(x["text"] for x in current)})
            current = [segment]
        else:
            current.append(segment)
    if current:
        clusters.append({"start": current[0]["start"], "end": current[-1]["end"], "text": " ".join(x["text"] for x in current)})
    return [item for item in clusters if item["end"] - item["start"] >= 4.0]


def _prepare_clips(clips: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for item in sorted(clips, key=lambda value: value["start"]):
        start = max(0.0, float(item["start"]) - 0.4)
        end = min(duration, float(item["end"]) + 0.6) if duration else float(item["end"]) + 0.6
        if end <= start:
            continue
        candidate = {**item, "start": round(start, 3), "end": round(end, 3)}
        if prepared and candidate["start"] - prepared[-1]["end"] <= 1.0:
            prepared[-1]["end"] = max(prepared[-1]["end"], candidate["end"])
            prepared[-1]["text"] = f"{prepared[-1].get('text', '')} {candidate.get('text', '')}".strip()
            prepared[-1]["final_score"] = max(prepared[-1].get("final_score", 0), candidate.get("final_score", 0))
        else:
            prepared.append(candidate)
    return prepared


def _select_clips(clusters: list[dict[str, Any]], target_seconds: int) -> list[dict[str, Any]]:
    candidates = [item for item in clusters if item["end"] - item["start"] >= 5.0]
    unit = 2
    target = target_seconds * unit
    upper = (target_seconds + 30) * unit
    states: dict[int, tuple[float, list[dict[str, Any]]]] = {0: (0.0, [])}
    for item in candidates:
        duration = max(1, round((item["end"] - item["start"]) * unit))
        value = float(item.get("final_score", 0.0))
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


def _ensure_opening_and_ending(
    selected: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    duration: float,
    genre: str = "ai_news",
) -> list[dict[str, Any]]:
    """Keep a meaningful topic opening and conclusion in the final edit."""

    if not candidates or duration <= 0:
        return selected

    opening_limit = min(duration * 0.20, 900.0)
    ending_limit = max(duration * 0.80, duration - 900.0)
    opening_pool = [item for item in candidates if float(item["start"]) <= opening_limit]
    ending_pool = [item for item in candidates if float(item["end"]) >= ending_limit]
    if genre == "ai_news":
        intro_phrases = (
            "시작하겠다",
            "시작하겠습니다",
            "소개하겠다",
            "소개하겠습니다",
            "메인뉴스",
            "메인 뉴스",
            "메인소식",
            "메인 소식",
        )
        intro_candidates = [
            item
            for item in opening_pool
            if any(phrase in str(item.get("text", "")) for phrase in intro_phrases)
        ]
        if intro_candidates:
            opening_pool = intro_candidates
    required: list[tuple[str, dict[str, Any] | None]] = [
        (
            "opening",
            max(opening_pool, key=lambda item: float(item.get("final_score", 0.0)))
            if opening_pool
            else None,
        ),
        (
            "ending",
            max(ending_pool, key=lambda item: float(item.get("final_score", 0.0)))
            if ending_pool
            else None,
        ),
    ]

    result = list(selected)
    for role, candidate in required:
        if candidate is None:
            continue
        overlaps = any(
            float(item["start"]) < float(candidate["end"])
            and float(candidate["start"]) < float(item["end"])
            for item in result
        )
        if overlaps:
            continue
        result.append({**candidate, "edit_role": role})

    return sorted(result, key=lambda item: float(item["start"]))


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
    offset_seconds: float = 0.0,
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
            # Positive offset means the subtitle should appear earlier.
            output_start = timeline_offset + start - clip_start - offset_seconds
            output_end = timeline_offset + end - clip_start - offset_seconds
            if output_end <= 0:
                continue
            output_start = max(0.0, output_start)
            if entries and text == entries[-1][2] and output_start <= entries[-1][1] + 0.2:
                entries[-1] = (entries[-1][0], max(entries[-1][1], output_end), text)
            else:
                entries.append((output_start, output_end, text))
        timeline_offset += max(0.0, clip_end - clip_start)

    lines: list[str] = []
    for index, (start, end, value) in enumerate(entries, start=1):
        wrapped = "\n".join(textwrap.wrap(value, width=38, break_long_words=False, break_on_hyphens=False))
        lines.extend([str(index), f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}", wrapped, ""])
    output.write_text("\n".join(lines), encoding="utf-8-sig")
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


def _burn_subtitles(
    source: Path,
    subtitles: Path,
    output: Path,
    font_name: str = "Malgun Gothic",
    font_size: int = 18,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise LiveEditPipelineError("ffmpeg가 설치되어 있지 않습니다.")
    filter_path = str(subtitles.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    safe_font_name = re.sub(r"[\\:'&,]", "", str(font_name)).strip() or "Malgun Gothic"
    safe_font_size = max(8, min(64, int(font_size)))
    subtitle_filter = (
        f"subtitles='{filter_path}':"
        f"force_style='FontName={safe_font_name},FontSize={safe_font_size},"
        "Outline=2,Shadow=1,MarginV=36'"
    )
    _run_ffmpeg([
        ffmpeg, "-y", "-i", str(source), "-vf", subtitle_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output),
    ])


def render_preview(
    source: Path,
    clips: list[dict[str, Any]],
    output: Path,
    subtitles: Path | None = None,
    font_name: str = "Malgun Gothic",
    font_size: int = 18,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise LiveEditPipelineError("ffmpeg가 설치되어 있지 않습니다.")
    with tempfile.TemporaryDirectory(prefix="live-edit-") as temp_name:
        temp = Path(temp_name)
        files = []
        for index, clip in enumerate(clips):
            segment = temp / f"segment-{index:04d}.mp4"
            _run_ffmpeg([
                ffmpeg, "-y", "-ss", str(clip["start"]), "-i", str(source),
                "-t", str(clip["end"] - clip["start"]), "-map", "0", "-c", "copy",
                "-avoid_negative_ts", "make_zero", str(segment),
            ])
            files.append(segment)
        concat = temp / "concat.txt"
        concat.write_text("\n".join(f"file '{path.as_posix()}'" for path in files), encoding="utf-8")
        joined = temp / "joined.mp4"
        _run_ffmpeg([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(joined)])
        if subtitles and subtitles.exists() and subtitles.stat().st_size > 0:
            _burn_subtitles(joined, subtitles, output, font_name, font_size)
        else:
            shutil.copyfile(joined, output)


def render_exact(
    source: Path,
    clips: list[dict[str, Any]],
    output: Path,
    subtitles: Path | None = None,
    font_name: str = "Malgun Gothic",
    font_size: int = 18,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise LiveEditPipelineError("ffmpeg가 설치되어 있지 않습니다.")
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
            ffmpeg, "-y", "-i", str(source), "-filter_complex", filter_graph,
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "fast",
            "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(joined),
        ])
        if subtitles and subtitles.exists() and subtitles.stat().st_size > 0:
            _burn_subtitles(joined, subtitles, output, font_name, font_size)
        else:
            shutil.copyfile(joined, output)


class LiveEditPipeline:
    def __init__(self, media_root: Path | None = None):
        self.media_root = (media_root or get_media_root()).resolve()

    def run(
        self,
        *,
        vod_url: str,
        archive_path: Path,
        genre: str = "ai_news",
        actual_start_time: str | None = None,
        target_seconds: int = 600,
        bucket_seconds: int = 30,
        delay_seconds: float = 0.0,
        subtitle_offset_seconds: float = -4.0,
        subtitle_font_name: str = "Malgun Gothic",
        subtitle_font_size: int = 18,
        render_mode: str = "preview",
        progress_callback: Callable[[int, str], None] | None = None,
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

        job_id = uuid4().hex
        output_dir = self.media_root / "youtube-live-edit" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        report(5, "YouTube 원본 영상과 자막을 다운로드하는 중입니다.")
        importer = YouTubeImporter(self.media_root)
        imported = importer._import_video_sync(vod_url, job_id=f"edit-{job_id}")
        report(18, "원본 영상과 자막 다운로드가 완료되었습니다.")
        source = Path(imported.get("video_path", ""))
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        if not source.exists():
            raise LiveEditPipelineError("yt-dlp가 원본 영상을 저장하지 못했습니다.")

        subtitle_candidates = [Path(value) for value in imported.get("subtitle_files", [])]
        subtitle_candidates = [path if path.is_absolute() else (Path.cwd() / path).resolve() for path in subtitle_candidates]
        subtitle = next((path for path in subtitle_candidates if path.suffix.lower() == ".vtt" and ".ko" in path.name), None)
        subtitle = subtitle or next((path for path in subtitle_candidates if path.suffix.lower() == ".vtt"), None)
        if not subtitle or not subtitle.exists():
            raise LiveEditPipelineError("yt-dlp로 한국어 자막을 가져오지 못했습니다. 자동 자막이 없는 영상은 Whisper fallback을 추가해야 합니다.")

        raw_segments = parse_vtt(subtitle)
        if not raw_segments:
            raise LiveEditPipelineError("자막 파일은 있지만 시간표시 문장을 읽지 못했습니다.")

        report(22, f"자막 {len(raw_segments):,}개 구간을 확인했습니다.")
        agents = GeminiAgents()
        cleaned_result = agents.clean_transcript(raw_segments)
        cleaned_segments = cleaned_result["segments"]
        report(40, "Gemini가 자막 오타와 중복을 정리했습니다.")
        summary = agents.compress_transcript(cleaned_segments)
        report(55, "Gemini가 줄거리와 주요 내용을 요약했습니다.")
        messages = _load_replay_messages(archive_path, actual_start_time, delay_seconds)
        clusters = _cluster_transcript(cleaned_segments)
        clusters = score_chat_density(clusters, messages, bucket_seconds=bucket_seconds)
        report(65, f"채팅 {len(messages):,}개를 시간대별 밀도로 계산했습니다.")

        for item in clusters:
            text_score = min(1000.0, max(0.0, len(item["text"]) * 18.0))
            item["preliminary_score"] = item["chat_score"] * 0.7 + text_score * 0.3
        max_llm_candidates = max(
            80,
            min(360, int(os.getenv("LIVE_EDIT_MAX_LLM_CANDIDATES", "180"))),
        )
        candidate_indexes = sorted(
            range(len(clusters)),
            key=lambda index: clusters[index]["preliminary_score"],
            reverse=True,
        )[:max_llm_candidates]
        # Always give the genre-specific agent access to the beginning and
        # ending of the source so the final edit can retain a meaningful
        # topic opening and conclusion.
        boundary_count = min(30, len(clusters))
        candidate_indexes = sorted(
            set(candidate_indexes)
            | set(range(boundary_count))
            | set(range(max(0, len(clusters) - boundary_count), len(clusters)))
        )
        candidates = [clusters[index] for index in candidate_indexes]
        scored = agents.score_clusters(candidates, summary, genre=genre)
        report(80, f"AI 후보 구간 {len(candidates):,}개를 평가했습니다.")
        for item in scored:
            item["final_score"] = round(
                item.get("llm_score", 0.0) * 0.55
                + item.get("chat_score", 0.0) * 0.35
                + min(1000.0, len(item.get("text", "")) * 18.0) * 0.10,
                3,
            )
        selected = _select_clips(scored, target_seconds)
        duration = float(imported.get("duration") or 0.0)
        selected = _ensure_opening_and_ending(selected, scored, duration, genre=genre)
        clips = _prepare_clips(selected, duration)
        if not clips:
            raise LiveEditPipelineError("편집할 하이라이트 구간을 선택하지 못했습니다.")

        report(88, f"최종 하이라이트 {len(clips):,}개 구간을 선택했습니다.")
        (output_dir / "raw_transcript.json").write_text(json.dumps({"segments": raw_segments}, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "cleaned_transcript.json").write_text(json.dumps(cleaned_result, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "scored_clusters.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
        generated_subtitles = output_dir / "subtitles.srt"
        subtitle_count = write_selected_subtitles(
            cleaned_segments,
            clips,
            generated_subtitles,
            offset_seconds=subtitle_offset_seconds,
        )
        plan = {
            "vod_url": vod_url,
            "genre": genre,
            "target_seconds": target_seconds,
            "chat_messages": len(messages),
            "subtitle_count": subtitle_count,
            "source_video_path": str(source.resolve()),
            "render_mode": render_mode,
            "subtitle_offset_seconds": subtitle_offset_seconds,
            "subtitle_font_name": subtitle_font_name,
            "subtitle_font_size": subtitle_font_size,
            "clips": clips,
        }
        (output_dir / "edit_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

        rendered = output_dir / f"edited-{render_mode}.mp4"
        if render_mode == "preview":
            render_preview(
                source, clips, rendered, generated_subtitles,
                subtitle_font_name, subtitle_font_size,
            )
        else:
            render_exact(
                source, clips, rendered, generated_subtitles,
                subtitle_font_name, subtitle_font_size,
            )
        report(100, "AI 영상 편집이 완료되었습니다.")
        return {
            "job_id": job_id,
            "vod_video_id": imported.get("job_id"),
            "genre": genre,
            "source_video_path": str(source),
            "subtitle_path": str(subtitle),
            "raw_transcript_path": str((output_dir / "raw_transcript.json").resolve()),
            "cleaned_transcript_path": str((output_dir / "cleaned_transcript.json").resolve()),
            "summary_path": str((output_dir / "summary.json").resolve()),
            "score_path": str((output_dir / "scored_clusters.json").resolve()),
            "edit_plan_path": str((output_dir / "edit_plan.json").resolve()),
            "generated_subtitles_path": str(generated_subtitles.resolve()),
            "subtitle_count": subtitle_count,
            "subtitles_burned_in": subtitle_count > 0,
            "rendered_video_path": str(rendered.resolve()),
            "render_mode": render_mode,
            "chat_message_count": len(messages),
            "broadcast_delay_seconds": delay_seconds,
            "subtitle_offset_seconds": subtitle_offset_seconds,
            "subtitle_font_name": subtitle_font_name,
            "subtitle_font_size": subtitle_font_size,
            "target_seconds": target_seconds,
            "selected_duration_seconds": round(sum(item["end"] - item["start"] for item in clips), 3),
            "summary": summary,
            "clips": clips,
        }

    def rerender_from_saved_subtitles(self, job_id: str) -> dict[str, Any]:
        """Re-render an existing edit after the user changes its SRT file."""

        output_dir = self.media_root / "youtube-live-edit" / job_id
        plan_path = output_dir / "edit_plan.json"
        subtitles = output_dir / "subtitles.srt"
        if not plan_path.exists() or not subtitles.exists():
            raise LiveEditPipelineError("편집 작업 또는 자막 파일을 찾을 수 없습니다.")

        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LiveEditPipelineError("편집 계획 파일을 읽을 수 없습니다.") from exc

        source = Path(str(plan.get("source_video_path", "")))
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        if not source.exists():
            raise LiveEditPipelineError("자막을 다시 입힐 원본 영상을 찾을 수 없습니다.")

        clips = plan.get("clips") or []
        render_mode = plan.get("render_mode", "preview")
        font_name = plan.get("subtitle_font_name", "Malgun Gothic")
        font_size = int(plan.get("subtitle_font_size", 18))
        output = output_dir / f"edited-{render_mode}.mp4"
        if render_mode == "preview":
            render_preview(source, clips, output, subtitles, font_name, font_size)
        elif render_mode == "exact":
            render_exact(source, clips, output, subtitles, font_name, font_size)
        else:
            raise LiveEditPipelineError("저장된 렌더링 방식이 올바르지 않습니다.")
        return {
            "job_id": job_id,
            "rendered_video_path": str(output.resolve()),
            "generated_subtitles_path": str(subtitles.resolve()),
            "render_mode": render_mode,
        }

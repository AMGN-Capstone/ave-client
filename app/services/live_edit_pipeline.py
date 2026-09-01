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
from bisect import bisect_right
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


def postprocess_downloaded_subtitles(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn YouTube rolling captions into non-overlapping transcript cues.

    The archived scripter removed exact adjacent duplicates, but current
    YouTube automatic captions normally use a rolling window instead: a new
    cue repeats the end of the previous cue and appends a few words.  This
    function performs that overlap removal before any optional LLM call.
    """

    def clean_text(value: object) -> str:
        text = str(value or "").replace("\u00a0", " ")
        text = re.sub(r"\[.*?\]|\(.*?\)", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def comparable_token(value: str) -> str:
        # Keep Korean/Latin/digit content and ignore punctuation-only changes
        # such as "합니다." -> "합니다" in adjacent rolling captions.
        return re.sub(r"[^\w가-힣]", "", value).casefold()

    def remove_rolling_prefix(previous: str, current: str) -> str:
        """Remove the longest suffix of previous repeated at current's head."""

        if not previous or not current:
            return current
        previous_normalized = re.sub(r"\s+", " ", previous).strip()
        current_normalized = re.sub(r"\s+", " ", current).strip()
        if current_normalized == previous_normalized:
            return ""
        if current_normalized.startswith(previous_normalized):
            return current_normalized[len(previous_normalized):].lstrip(" ,.!?;:")

        previous_words = previous_normalized.split()
        current_words = current_normalized.split()
        maximum = min(len(previous_words), len(current_words))
        # One shared short word ("네", "그") is often a genuine new turn;
        # require two words unless the entire prior cue is repeated above.
        for size in range(maximum, 1, -1):
            left = [comparable_token(word) for word in previous_words[-size:]]
            right = [comparable_token(word) for word in current_words[:size]]
            if left == right and all(left):
                return " ".join(current_words[size:]).lstrip(" ,.!?;:")
        return current_normalized

    processed: list[dict[str, Any]] = []
    previous_source_text = ""
    for segment in segments:
        source_text = clean_text(segment.get("text", ""))
        if not source_text:
            continue
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue

        # yt-dlp writes 0.01-second commit cues between rolling windows. They
        # contain no new readable subtitle content and must not become input
        # to summary/scoring when Gemini cleanup is switched off.
        if end - start <= 0.05:
            continue

        text = remove_rolling_prefix(previous_source_text, source_text)
        previous_source_text = source_text
        if not text:
            if processed:
                processed[-1]["end"] = max(float(processed[-1]["end"]), end)
            continue
        processed.append({**segment, "start": start, "end": end, "text": text})
    return processed


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
        # Some yt-dlp VTTs have an empty line between the timing line and the
        # caption. A blank line is therefore not a reliable cue delimiter;
        # the next timing line is.
        while index < len(lines) and not _VTT_TIME.search(lines[index]):
            if lines[index].strip():
                text_lines.append(lines[index].strip())
            index += 1
        text = re.sub(r"<[^>]+>", "", html.unescape(" ".join(text_lines))).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            start = _time_seconds(match.group("start"))
            end = _time_seconds(match.group("end"))
            if end > start:
                segments.append({"start": start, "end": end, "text": text})
    return postprocess_downloaded_subtitles(segments)


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
        # pytchat replay records already provide elapsed_seconds, whereas old
        # archives use absolute publish time. Apply the same user correction
        # to either representation.
        elapsed -= delay_seconds
        if elapsed >= 0:
            records.append({**item, "elapsed_seconds": elapsed})
    return records


def score_chat_density(
    segments: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Measure chat density in each actual transcript-cluster time span."""

    weighted_messages = [
        (float(item["elapsed_seconds"]), 1.5 if item.get("super_chat") else 1.0)
        for item in messages
    ]
    rates = []
    for segment in segments:
        duration = max(0.5, float(segment["end"]) - float(segment["start"]))
        weighted_count = sum(
            weight for elapsed, weight in weighted_messages
            if float(segment["start"]) <= elapsed < float(segment["end"])
        )
        rates.append(weighted_count * 60.0 / duration)
    scale = max(_percentile(rates, 0.9), 1.0)

    result = []
    for item in segments:
        duration = max(0.5, float(item["end"]) - float(item["start"]))
        weighted_count = sum(
            weight for elapsed, weight in weighted_messages
            if float(item["start"]) <= elapsed < float(item["end"])
        )
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


def _finite_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
    except OverflowError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _merge_timed_summary_chapters(
    chapters: list[dict[str, Any]],
    target_count: int,
) -> list[dict[str, Any]]:
    """Merge adjacent model chapters without losing the final topics."""

    if len(chapters) <= target_count:
        return chapters
    merged: list[dict[str, Any]] = []
    base_size, extra = divmod(len(chapters), target_count)
    cursor = 0
    for index in range(target_count):
        size = base_size + (1 if index < extra else 0)
        items = chapters[cursor : cursor + size]
        cursor += size
        titles = [str(item.get("title", "")).strip() for item in items]
        titles = [value for value in titles if value]
        summaries = [str(item.get("summary", "")).strip() for item in items]
        summaries = [value for value in summaries if value]
        merged.append(
            {
                "title": (
                    titles[0]
                    if len(titles) <= 1
                    else f"{titles[0]} – {titles[-1]}"
                ) if titles else "",
                "summary": " ".join(summaries)[:500],
                "start": min(float(item["start"]) for item in items),
                "end": max(float(item["end"]) for item in items),
            }
        )
    return merged


def _build_timed_candidate_chapters(
    ordered: list[dict[str, Any]],
    summary_chapters: list[dict[str, Any]],
    *,
    max_chapters: int,
    max_segments_per_chapter: int | None,
    source_duration: float | None,
) -> list[dict[str, Any]] | None:
    """Assign candidates using model topic boundaries, or return None to fall back."""

    timed: list[dict[str, Any]] = []
    for item in summary_chapters:
        start = _finite_seconds(item.get("start"))
        end = _finite_seconds(item.get("end"))
        if start is None or end is None or end <= start:
            return None
        timed.append({**item, "start": start, "end": end})
    if not timed:
        return None

    timed.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    if any(
        float(right["start"]) <= float(left["start"])
        or float(right["end"]) <= float(left["end"])
        for left, right in zip(timed, timed[1:])
    ):
        return None
    if source_duration and source_duration > 0:
        if any(
            float(item["start"]) > source_duration + 1.0
            or float(item["end"]) > source_duration + 1.0
            for item in timed
        ):
            return None
    target_labels = min(max_chapters, len(ordered), len(timed))
    timed = _merge_timed_summary_chapters(timed, target_labels)

    candidate_start = min(float(item["start"]) for item in ordered)
    candidate_end = max(float(item["end"]) for item in ordered)
    if max(float(item["end"]) for item in timed) < candidate_start:
        return None
    if min(float(item["start"]) for item in timed) > candidate_end:
        return None

    boundaries = [
        (float(left["end"]) + float(right["start"])) / 2.0
        for left, right in zip(timed, timed[1:])
    ]
    if any(
        not math.isfinite(value)
        or (index > 0 and value <= boundaries[index - 1])
        for index, value in enumerate(boundaries)
    ):
        return None

    semantic_groups: list[list[dict[str, Any]]] = [[] for _ in timed]
    for item in ordered:
        midpoint = (float(item["start"]) + float(item["end"])) / 2.0
        semantic_groups[bisect_right(boundaries, midpoint)].append(item)

    populated = [
        (label, group)
        for label, group in zip(timed, semantic_groups)
        if group
    ]
    if not populated:
        return None

    segment_limit = len(ordered) if max_segments_per_chapter is None else max(1, max_segments_per_chapter)
    desired_count = min(
        max_chapters,
        len(ordered),
        max(
            len(populated),
            sum(
                math.ceil(len(group) / segment_limit)
                for _, group in populated
            ),
        ),
    )
    part_counts = [1 for _ in populated]
    while sum(part_counts) < desired_count:
        splittable = [
            index
            for index, (_, group) in enumerate(populated)
            if part_counts[index] < len(group)
        ]
        if not splittable:
            break
        split_index = max(
            splittable,
            key=lambda index: len(populated[index][1]) / part_counts[index],
        )
        part_counts[split_index] += 1

    result: list[dict[str, Any]] = []
    for (label, group), part_count in zip(populated, part_counts):
        base_size, extra = divmod(len(group), part_count)
        cursor = 0
        for part_index in range(part_count):
            size = base_size + (1 if part_index < extra else 0)
            part = group[cursor : cursor + size]
            cursor += size
            source_title = str(label.get("title", "")).strip()
            if part_count > 1 and source_title:
                source_title = f"{source_title} · {part_index + 1}/{part_count}"
            fallback_text = re.sub(r"\s+", " ", str(part[0].get("text", ""))).strip()
            fallback_summary = " ".join(
                re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
                for item in part[:2]
            ).strip()
            result.append(
                {
                    "chapter_id": f"chapter-{len(result):02d}",
                    "title": source_title or fallback_text[:32] or f"챕터 {len(result) + 1}",
                    "summary": str(label.get("summary", "")).strip() or fallback_summary[:180],
                    "start": min(float(item["start"]) for item in part),
                    "end": max(float(item["end"]) for item in part),
                    "segment_ids": [str(item["segment_id"]) for item in part],
                }
            )
    return result


def _build_candidate_chapters(
    candidates: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
    *,
    max_chapters: int | None = None,
    max_segments_per_chapter: int | None = None,
    source_duration: float | None = None,
) -> list[dict[str, Any]]:
    """Group chronological AI candidates into a small reviewable chapter list.

    The existing transcript summary already contains ordered chapter labels, so
    this intentionally avoids another model request. Candidate IDs remain the
    source of truth and every candidate is assigned to exactly one chapter.
    """

    _ensure_candidate_ids(candidates)
    ordered = sorted(candidates, key=lambda item: float(item.get("start", 0.0)))
    if not ordered:
        return []
    max_chapters = len(ordered) if max_chapters is None else max(1, min(int(max_chapters), len(ordered)))
    max_segments_per_chapter = None if max_segments_per_chapter is None else max(1, int(max_segments_per_chapter))
    summary_data = summary if isinstance(summary, dict) else {}
    raw_summary_chapters = summary_data.get("chapters") or []
    if not isinstance(raw_summary_chapters, list):
        raw_summary_chapters = []
    summary_chapters = [
        item
        for item in raw_summary_chapters
        if isinstance(item, dict)
    ]
    timed_result = _build_timed_candidate_chapters(
        ordered,
        summary_chapters,
        max_chapters=max_chapters,
        max_segments_per_chapter=max_segments_per_chapter,
        source_duration=source_duration,
    )
    if timed_result is not None:
        return timed_result

    minimum_for_readability = 1 if max_segments_per_chapter is None else math.ceil(len(ordered) / max_segments_per_chapter)
    desired_count = max(1, minimum_for_readability, len(summary_chapters))
    chapter_count = min(max_chapters, len(ordered), desired_count)

    if len(summary_chapters) > chapter_count:
        # Merge adjacent model summaries instead of silently dropping later
        # topics when the model returned more labels than the UI cap.
        summary_groups: list[dict[str, str]] = []
        summary_base, summary_extra = divmod(len(summary_chapters), chapter_count)
        summary_cursor = 0
        for index in range(chapter_count):
            size = summary_base + (1 if index < summary_extra else 0)
            items = summary_chapters[summary_cursor : summary_cursor + size]
            summary_cursor += size
            titles = [str(item.get("title", "")).strip() for item in items]
            titles = [value for value in titles if value]
            summaries = [str(item.get("summary", "")).strip() for item in items]
            summaries = [value for value in summaries if value]
            summary_groups.append(
                {
                    "title": (
                        titles[0]
                        if len(titles) <= 1
                        else f"{titles[0]} – {titles[-1]}"
                    ) if titles else "",
                    "summary": " ".join(summaries)[:500],
                }
            )
        summary_chapters = summary_groups
    summary_indexes = [
        min(len(summary_chapters) - 1, int(index * len(summary_chapters) / chapter_count))
        if summary_chapters
        else -1
        for index in range(chapter_count)
    ]
    summary_totals = {
        value: summary_indexes.count(value)
        for value in set(summary_indexes)
        if value >= 0
    }
    summary_seen: dict[int, int] = {}

    base_size, extra = divmod(len(ordered), chapter_count)
    result: list[dict[str, Any]] = []
    cursor = 0
    for index in range(chapter_count):
        size = base_size + (1 if index < extra else 0)
        group = ordered[cursor : cursor + size]
        cursor += size
        summary_index = summary_indexes[index]
        summary_item = summary_chapters[summary_index] if summary_index >= 0 else {}
        source_title = str(summary_item.get("title", "")).strip()
        source_summary = str(summary_item.get("summary", "")).strip()
        if summary_index >= 0:
            summary_seen[summary_index] = summary_seen.get(summary_index, 0) + 1
            if summary_totals.get(summary_index, 1) > 1 and source_title:
                source_title = (
                    f"{source_title} · {summary_seen[summary_index]}/"
                    f"{summary_totals[summary_index]}"
                )
        fallback_text = re.sub(r"\s+", " ", str(group[0].get("text", ""))).strip()
        title = source_title or fallback_text[:32] or f"챕터 {index + 1}"
        fallback_summary = " ".join(
            re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
            for item in group[:2]
        ).strip()
        chapter_summary = source_summary or fallback_summary[:180]
        result.append(
            {
                "chapter_id": f"chapter-{index:02d}",
                "title": title,
                "summary": chapter_summary,
                "start": float(group[0]["start"]),
                "end": float(group[-1]["end"]),
                "segment_ids": [str(item["segment_id"]) for item in group],
            }
        )
    return result


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
    progress_callback: Callable[[float], None] | None = None,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise LiveEditPipelineError("ffmpeg가 설치되어 있지 않습니다.")
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
                ffmpeg, "-y", "-i", str(source), "-ss", str(clip["start"]),
                "-t", str(clip["end"] - clip["start"]), "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
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
            _burn_subtitles(joined, subtitles, output, font_name, font_size)
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
        if progress_callback:
            progress_callback(0.82)
        if subtitles and subtitles.exists() and subtitles.stat().st_size > 0:
            _burn_subtitles(joined, subtitles, output, font_name, font_size)
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
        archive_path: Path,
        genre: str = "ai_news",
        llm_provider: str = "gemini",
        actual_start_time: str | None = None,
        target_seconds: int = 600,
        chat_delay_seconds: float = 0.0,
        clean_subtitles: bool = False,
        delay_seconds: float = 0.0,
        subtitle_offset_seconds: float = 0.0,
        subtitle_font_name: str = "Malgun Gothic",
        subtitle_font_size: int = 18,
        render_mode: str = "preview",
        defer_render: bool = False,
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

        job_id = job_id or uuid4().hex
        if Path(job_id).name != job_id:
            raise LiveEditPipelineError("잘못된 편집 작업 ID입니다.")
        output_dir = self.media_root / "youtube-live-edit" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        report(5, "기존 원본 영상과 자막을 확인하는 중입니다.")
        importer = YouTubeImporter(self.media_root)
        imported = importer._import_video_sync(vod_url, job_id=f"edit-{job_id}")
        report(
            18,
            "기존 원본 영상과 자막을 재사용합니다."
            if imported.get("cache_hit")
            else "원본 영상과 자막 다운로드가 완료되었습니다.",
        )
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

        raw_segments = [{**segment, "id": index} for index, segment in enumerate(raw_segments)]
        report(22, f"자막 {len(raw_segments):,}개 구간을 확인했습니다.")
        agents = GeminiAgents(provider=llm_provider)
        provider_label = {"gemini": "Gemini", "deepseek": "DeepSeek"}.get(llm_provider, llm_provider)
        cleaned_result = (
            agents.clean_transcript(raw_segments)
            if clean_subtitles
            else {"segments": raw_segments, "removed_count": 0, "skipped": True}
        )
        cleaned_segments = cleaned_result["segments"]
        report(40, f"{provider_label} 자막 정제를 생략했습니다." if not clean_subtitles else f"{provider_label}가 자막 오타와 중복을 정리했습니다.")
        summary = agents.compress_transcript(cleaned_segments)
        # LLMs return stable subtitle IDs, never timestamps. Resolve their
        # boundaries only after validation on the server.
        subtitle_by_id = {int(item["id"]): item for item in cleaned_segments}
        for chapter in summary.get("chapters", []) if isinstance(summary, dict) else []:
            if not isinstance(chapter, dict):
                continue
            try:
                first = subtitle_by_id[int(chapter.get("start_id"))]
                last = subtitle_by_id[int(chapter.get("end_id"))]
            except (KeyError, TypeError, ValueError):
                continue
            if float(last["end"]) >= float(first["start"]):
                chapter["start"] = float(first["start"])
                chapter["end"] = float(last["end"])
        report(55, f"{provider_label}가 줄거리와 주요 내용을 요약했습니다.")
        total_chat_delay = delay_seconds + chat_delay_seconds
        messages = _load_replay_messages(archive_path, actual_start_time, total_chat_delay)
        clusters = _cluster_transcript(cleaned_segments)
        clusters = score_chat_density(clusters, messages)
        report(65, f"채팅 {len(messages):,}개를 스크립트 구간별 밀도로 계산했습니다.")

        # Do not pre-filter by chat density or caption length: every script
        # cluster receives the same semantic LLM review.
        scored = agents.score_clusters(clusters, summary, genre=genre)
        report(80, f"{provider_label}가 전체 스크립트 구간 {len(scored):,}개를 평가했습니다.")
        for item in scored:
            item["final_score"] = round(float(item.get("llm_score", 0.0)), 3)
        # Stable IDs let the browser send a compact, auditable selection back
        # without trusting client-provided timestamps or text.
        _ensure_candidate_ids(scored)
        duration = float(imported.get("duration") or 0.0)
        chapters = _build_candidate_chapters(
            scored,
            summary,
            source_duration=duration,
        )
        selected = _select_clips(scored, target_seconds)
        selected = _ensure_opening_and_ending(selected, scored, duration, genre=genre)
        recommended_segment_ids = [str(item["segment_id"]) for item in selected]
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
            "llm_provider": llm_provider,
            "target_seconds": target_seconds,
            "chat_messages": len(messages),
            "subtitle_count": subtitle_count,
            "source_video_path": str(source.resolve()),
            "render_mode": render_mode,
            "subtitle_offset_seconds": subtitle_offset_seconds,
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
        (output_dir / "edit_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

        base_result = {
            "job_id": job_id,
            "vod_video_id": imported.get("job_id"),
            "genre": genre,
            "llm_provider": llm_provider,
            "source_video_path": str(source),
            "subtitle_path": str(subtitle),
            "raw_transcript_path": str((output_dir / "raw_transcript.json").resolve()),
            "cleaned_transcript_path": str((output_dir / "cleaned_transcript.json").resolve()),
            "summary_path": str((output_dir / "summary.json").resolve()),
            "score_path": str((output_dir / "scored_clusters.json").resolve()),
            "edit_plan_path": str((output_dir / "edit_plan.json").resolve()),
            "generated_subtitles_path": str(generated_subtitles.resolve()),
            "subtitle_count": subtitle_count,
            "subtitles_burned_in": False,
            "rendered_video_path": None,
            "render_mode": render_mode,
            "chat_message_count": len(messages),
            "chat_delay_seconds": total_chat_delay,
            "subtitle_offset_seconds": subtitle_offset_seconds,
            "subtitle_font_name": subtitle_font_name,
            "subtitle_font_size": subtitle_font_size,
            "target_seconds": target_seconds,
            "selected_duration_seconds": round(sum(item["end"] - item["start"] for item in clips), 3),
            "summary": summary,
            "clips": clips,
            "awaiting_selection": defer_render,
        }
        if defer_render:
            report(90, "AI 분석이 완료되었습니다. 웹에서 원하는 구간을 선택하세요.")
            return base_result

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
            **base_result,
            "subtitles_burned_in": subtitle_count > 0,
            "rendered_video_path": str(rendered.resolve()),
            "awaiting_selection": False,
        }

    def get_segment_review(self, job_id: str) -> dict[str, Any]:
        """Return browser-safe candidates and the current user selection."""

        output_dir, plan = self._load_edit_plan(job_id)
        candidates = plan.get("candidates") or []
        if not candidates:
            score_path = output_dir / "scored_clusters.json"
            try:
                candidates = json.loads(score_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LiveEditPipelineError("검토할 AI 후보 구간을 찾을 수 없습니다.") from exc
        _ensure_candidate_ids(candidates)

        selected_ids = [str(value) for value in plan.get("selected_segment_ids") or []]
        if not selected_ids:
            # Backward compatibility for plans generated before segment IDs
            # were persisted: infer selection by overlap with rendered clips.
            clips = plan.get("clips") or []
            selected_ids = [
                str(item["segment_id"])
                for item in candidates
                if any(
                    float(item["start"]) < float(clip["end"])
                    and float(clip["start"]) < float(item["end"])
                    for clip in clips
                )
            ]
        recommended_ids = [
            str(value)
            for value in plan.get("recommended_segment_ids") or selected_ids
        ]
        selected_set = set(selected_ids)
        candidate_ids = {str(item.get("segment_id")) for item in candidates}
        chapters = plan.get("chapters") or []
        stored_chapters_valid = isinstance(chapters, list) and all(
            isinstance(chapter, dict)
            and isinstance(chapter.get("segment_ids"), list)
            for chapter in chapters
        )
        assigned_ids = (
            [
                str(segment_id)
                for chapter in chapters
                for segment_id in chapter["segment_ids"]
            ]
            if stored_chapters_valid
            else []
        )
        if (
            not stored_chapters_valid
            or set(assigned_ids) != candidate_ids
            or len(assigned_ids) != len(candidate_ids)
        ):
            summary_path = output_dir / "summary.json"
            try:
                saved_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                saved_summary = {}
            chapters = _build_candidate_chapters(
                candidates,
                saved_summary,
                source_duration=float(plan.get("source_duration_seconds") or 0.0),
            )
        chapter_by_segment = {
            str(segment_id): str(chapter.get("chapter_id", ""))
            for chapter in chapters
            for segment_id in chapter.get("segment_ids", [])
        }
        public_fields = {
            "segment_id",
            "start",
            "end",
            "text",
            "llm_score",
            "chat_score",
            "final_score",
            "chat_count",
            "chat_density",
            "reason",
        }
        public_candidates = [
            {
                **{key: value for key, value in item.items() if key in public_fields},
                "chapter_id": chapter_by_segment.get(str(item.get("segment_id")), ""),
                "selected": str(item.get("segment_id")) in selected_set,
            }
            for item in sorted(candidates, key=lambda value: float(value.get("start", 0.0)))
        ]
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
            "segments": public_candidates,
            "source_video_url": f"/api/youtube/edit/{job_id}/media/source",
            "rendered_video_url": f"/api/youtube/edit/{job_id}/media/rendered",
            "render_mode": render_mode,
        }

    def rerender_from_selection(
        self,
        job_id: str,
        segment_ids: list[str],
        *,
        feedback: str | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        """Render an existing analysis again from user-selected candidates."""

        def report(progress: int, message: str) -> None:
            if progress_callback:
                progress_callback(progress, message)

        output_dir, plan = self._load_edit_plan(job_id)
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
        duration = float(plan.get("source_duration_seconds") or 0.0)
        clips = _prepare_clips(selected, duration)
        if not clips:
            raise LiveEditPipelineError("선택한 구간에서 유효한 편집 범위를 만들지 못했습니다.")

        cleaned_path = output_dir / "cleaned_transcript.json"
        try:
            cleaned = json.loads(cleaned_path.read_text(encoding="utf-8"))
            cleaned_segments = cleaned["segments"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LiveEditPipelineError("정제된 자막 파일을 읽을 수 없습니다.") from exc

        report(0, "선택한 구간에 맞춰 자막 시간축을 다시 만드는 중입니다.")
        pending_subtitles = output_dir / "subtitles.pending.srt"
        subtitle_count = write_selected_subtitles(
            cleaned_segments,
            clips,
            pending_subtitles,
            offset_seconds=float(plan.get("subtitle_offset_seconds") or 0.0),
        )

        render_mode = str(plan.get("render_mode", "preview"))
        font_name = str(plan.get("subtitle_font_name", "Malgun Gothic"))
        font_size = int(plan.get("subtitle_font_size", 18))
        revision = int(plan.get("revision") or 0) + 1
        output = output_dir / f"edited-{render_mode}-r{revision}.mp4"
        pending_output = output_dir / f"edited-{render_mode}-r{revision}.pending.mp4"
        report(0, f"사용자가 선택한 {len(canonical_ids):,}개 구간을 렌더링하는 중입니다.")
        def report_render_progress(fraction: float) -> None:
            percent = max(0, min(98, int(round(float(fraction) * 98))))
            report(percent, f"영상 렌더링 진행률 {percent}%")

        try:
            if render_mode == "preview":
                render_preview(
                    source,
                    clips,
                    pending_output,
                    pending_subtitles,
                    font_name,
                    font_size,
                    progress_callback=report_render_progress,
                )
            elif render_mode == "exact":
                render_exact(
                    source,
                    clips,
                    pending_output,
                    pending_subtitles,
                    font_name,
                    font_size,
                    progress_callback=report_render_progress,
                )
            else:
                raise LiveEditPipelineError("저장된 렌더링 방식이 올바르지 않습니다.")
        except Exception:
            pending_output.unlink(missing_ok=True)
            pending_subtitles.unlink(missing_ok=True)
            raise

        subtitles = output_dir / "subtitles.srt"
        os.replace(pending_output, output)
        os.replace(pending_subtitles, subtitles)
        plan.update(
            {
                "clips": clips,
                "selected_segment_ids": canonical_ids,
                "subtitle_count": subtitle_count,
                "revision": revision,
                "rendered_filename": output.name,
                "last_feedback": (feedback or "").strip() or None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        pending_plan = output_dir / "edit_plan.pending.json"
        pending_plan.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(pending_plan, output_dir / "edit_plan.json")

        history_path = output_dir / "segment_revisions.json"
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except (OSError, json.JSONDecodeError):
            history = []
        history.append(
            {
                "revision": revision,
                "created_at": plan["updated_at"],
                "segment_ids": canonical_ids,
                "selected_duration_seconds": round(
                    sum(float(item["end"]) - float(item["start"]) for item in clips),
                    3,
                ),
                "feedback": plan["last_feedback"],
            }
        )
        history_path.write_text(
            json.dumps(history[-100:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report(100, "선택한 구간으로 영상을 다시 만들었습니다.")
        return {
            **self.get_segment_review(job_id),
            "rendered_video_path": str(output.resolve()),
            "generated_subtitles_path": str(subtitles.resolve()),
            "subtitle_count": subtitle_count,
            "message": "선택한 구간으로 영상을 다시 생성했습니다.",
        }

    def _load_edit_plan(self, job_id: str) -> tuple[Path, dict[str, Any]]:
        if not job_id or Path(job_id).name != job_id:
            raise LiveEditPipelineError("잘못된 편집 작업 ID입니다.")
        output_dir = self.media_root / "youtube-live-edit" / job_id
        plan_path = output_dir / "edit_plan.json"
        if not plan_path.exists():
            raise LiveEditPipelineError("편집 계획 파일을 찾을 수 없습니다.")
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LiveEditPipelineError("편집 계획 파일을 읽을 수 없습니다.") from exc
        if not isinstance(plan, dict):
            raise LiveEditPipelineError("편집 계획 형식이 올바르지 않습니다.")
        return output_dir, plan

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
        revision = int(plan.get("revision") or 0) + 1
        output = output_dir / f"edited-{render_mode}-r{revision}.mp4"
        if render_mode == "preview":
            render_preview(source, clips, output, subtitles, font_name, font_size)
        elif render_mode == "exact":
            render_exact(source, clips, output, subtitles, font_name, font_size)
        else:
            raise LiveEditPipelineError("저장된 렌더링 방식이 올바르지 않습니다.")
        plan.update(
            {
                "revision": revision,
                "rendered_filename": output.name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        pending_plan = output_dir / "edit_plan.pending.json"
        pending_plan.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(pending_plan, plan_path)
        return {
            "job_id": job_id,
            "rendered_video_path": str(output.resolve()),
            "generated_subtitles_path": str(subtitles.resolve()),
            "render_mode": render_mode,
            "rendered_video_url": f"/api/youtube/edit/{job_id}/media/rendered",
            "revision": revision,
        }

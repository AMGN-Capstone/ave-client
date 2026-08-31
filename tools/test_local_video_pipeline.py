"""Manual test for non-YouTube video transcription and edit planning.

Run from the test_yt_web directory:
    python -m tools.test_local_video_pipeline media/uploads/example.mp4
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.local_video_pipeline import analyze_local_video


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a local video with Whisper and Gemini")
    parser.add_argument("video", type=Path, help="Path to a local video file")
    parser.add_argument("--model", default=None, help="Whisper model, e.g. small or large-v3")
    parser.add_argument("--device", default=None, choices=["cpu", "cuda"])
    parser.add_argument("--compute-type", default=None, help="Whisper compute type, e.g. int8 or float16")
    parser.add_argument("--gemini-model", default=None)
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    args = parser.parse_args()

    result = analyze_local_video(
        args.video,
        whisper_model=args.model,
        whisper_device=args.device,
        whisper_compute_type=args.compute_type,
        gemini_model=args.gemini_model,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
        print(f"Saved result to: {args.output.resolve()}")


if __name__ == "__main__":
    main()

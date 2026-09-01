"""Gemini-based edit-plan generation from a timestamped transcript."""

from __future__ import annotations

import json
import os
from typing import Any

EDIT_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "action": {"type": "string", "enum": ["keep", "remove"]},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["start", "end", "action", "reason", "confidence"],
            },
        },
    },
    "required": ["title", "summary", "segments"],
}


class GeminiEditPlanError(RuntimeError):
    """Raised when Gemini cannot return a valid edit plan."""


class GeminiEditor:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        if not self.api_key:
            raise GeminiEditPlanError(
                "GEMINI_API_KEY is not configured. Add it to test_yt_web/token.env."
            )

        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.timeout = timeout
        self.url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )

    def create_edit_plan(
        self,
        transcript: dict[str, Any],
        *,
        max_segments: int = 40,
    ) -> dict[str, Any]:
        try:
            import requests
        except ImportError as exc:
            raise GeminiEditPlanError(
                "requests is not installed. Run: pip install -r requirements.txt"
            ) from exc

        duration = float(transcript.get("duration", 0.0))
        transcript_text = "\n".join(
            f"[{item['start']:.3f} - {item['end']:.3f}] {item['text']}"
            for item in transcript.get("segments", [])
        )
        if not transcript_text.strip():
            raise GeminiEditPlanError("Transcript is empty.")

        prompt = f"""
You are an automatic video-editing planner.
Analyze the Korean transcript below and produce an edit plan for a concise,
information-dense highlight video.

Rules:
- Use only timestamps that exist in the transcript.
- Keep important explanations, conclusions, reactions, and high-information moments.
- Remove silence, greetings, repeated sentences, filler words, and off-topic parts.
- Do not invent events or timestamps.
- Return at most {max_segments} segments.
- The output must follow the supplied JSON schema.
- The original video duration is {duration:.3f} seconds.

Transcript:
{transcript_text}
""".strip()

        payload = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": "Return only a valid JSON edit plan. Never include markdown fences."
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
                "responseSchema": EDIT_PLAN_SCHEMA,
            },
        }

        try:
            response = requests.post(
                self.url,
                params={"key": self.api_key},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            raw_text = body["candidates"][0]["content"]["parts"][0]["text"]
            plan = json.loads(raw_text)
        except requests.RequestException as exc:
            raise GeminiEditPlanError(f"Gemini API request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise GeminiEditPlanError(
                "Gemini returned an unexpected or invalid JSON response."
            ) from exc

        return self._validate_plan(plan, duration)

    @staticmethod
    def _validate_plan(plan: Any, duration: float) -> dict[str, Any]:
        if not isinstance(plan, dict) or not isinstance(plan.get("segments"), list):
            raise GeminiEditPlanError("Edit plan must contain a segments array.")

        validated_segments = []
        for item in plan["segments"]:
            if not isinstance(item, dict):
                continue
            try:
                start = max(0.0, float(item["start"]))
                end = min(duration, float(item["end"])) if duration else float(item["end"])
                confidence = min(1.0, max(0.0, float(item.get("confidence", 0.0))))
            except (KeyError, TypeError, ValueError):
                continue

            if end <= start:
                continue
            if item.get("action") not in {"keep", "remove"}:
                continue

            validated_segments.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "action": item["action"],
                    "reason": str(item.get("reason", "")),
                    "confidence": round(confidence, 3),
                }
            )

        return {
            "title": str(plan.get("title", "")),
            "summary": str(plan.get("summary", "")),
            "segments": validated_segments,
        }

"""Gemini agents used by the YouTube live-video editing pipeline."""

from __future__ import annotations

import json
import hashlib
import os
import random
import re
import time
from pathlib import Path
from typing import Any


class GeminiAgentError(RuntimeError):
    """Raised when a Gemini agent cannot produce a usable response."""


GENRE_LABELS = {
    "ai_news": "AI 뉴스",
    "stock": "주식·증시",
    "game": "게임",
}

GENRE_HIGHLIGHT_SYSTEM_PROMPTS = {
    "ai_news": """당신은 AI·기술 뉴스 롱폼 영상의 하이라이트 심사 에이전트입니다.
새로운 AI 모델·제품·연구·정책·기업 발표처럼 시청자가 알아야 할 새로운 사실,
핵심 주장과 근거, 기술적 차이, 산업·사회적 영향을 설명하는 구간을 높게 평가하세요.
사건의 배경과 결과가 연결되고, 해당 구간만 보아도 핵심을 이해할 수 있는 구간을 우선하세요.
인사, 방송 시작 멘트, 광고, 같은 헤드라인의 반복, 근거 없는 추측, 단순 감탄과 침묵은 낮게 평가하세요.
영상 자막에 없는 사실을 추론하거나 추가하지 말고, 뉴스의 사실과 진행자의 의견을 구분하세요.
편집본에는 주제가 본격적으로 시작되는 오프닝 구간과 핵심 내용을 정리하거나 마무리하는 엔딩 구간이
반드시 포함되어야 합니다. 단순 인사만 있는 첫 구간은 오프닝으로 보지 말고, 주제 소개가 시작되는
구간을 우선하세요. 특히 스크립트에 '시작하겠다', '시작하겠습니다', '소개하겠다',
'소개하겠습니다', '메인뉴스', '메인 뉴스', '메인 소식' 같은 표현이 있으면 해당 구간을
AI 뉴스 인트로로 간주하고 반드시 오프닝 후보에 포함하세요. 엔딩은 결론·전망·정리 발언이
있는 마지막 유의미한 구간을 우선하세요.
반드시 JSON {\"items\":[{\"id\":number,\"score\":number,\"reason\":string}]}만 반환하세요.
score는 0부터 1000 사이입니다.""",
    "stock": """당신은 주식·증시 분석 롱폼 영상의 하이라이트 심사 에이전트입니다.
주가·수익률·실적·매출·금리·물가·고용·환율 같은 구체적 수치와 변화,
시장에 영향을 주는 원인·촉매·위험요인, 종목이나 지수에 대한 논리적 분석을 높게 평가하세요.
숫자와 단위가 정확하고, 왜 움직였는지와 앞으로의 주요 변수가 설명되는 구간을 우선하세요.
인사, 종목명만 반복하는 구간, 매수·매도 구호, 근거 없는 예측, 광고, 침묵과 단순 시세 낭독은 낮게 평가하세요.
자막에 없는 투자 판단이나 수치를 만들지 말고, 진행자의 전망을 확정적 사실처럼 바꾸지 마세요.
편집본에는 주제가 본격적으로 시작되는 오프닝 구간과 핵심 내용을 정리하거나 마무리하는 엔딩 구간이
반드시 포함되어야 합니다. 단순 인사만 있는 첫 구간은 오프닝으로 보지 말고, 첫 종목·지표·시장 이슈가
실제로 설명되기 시작하는 구간을 우선하세요. 엔딩은 결론·전망·위험요인 정리 발언이 있는 구간을 우선하세요.
반드시 JSON {\"items\":[{\"id\":number,\"score\":number,\"reason\":string}]}만 반환하세요.
score는 0부터 1000 사이입니다.""",
    "game": """당신은 게임 플레이·게임 방송 롱폼 영상의 하이라이트 심사 에이전트입니다.
승패를 가른 장면, 결정적 플레이, 전략·판단·전술, 보스·아이템·업데이트 정보,
플레이어의 실수와 반전, 시청자가 재미나 긴장감을 느낄 만한 리액션을 높게 평가하세요.
장면의 원인과 결과가 이어지고, 플레이 상황을 이해할 수 있는 구간을 우선하세요.
로딩, 메뉴 이동, 대기, 침묵, 같은 행동의 반복, 의미 없는 인사와 추임새는 낮게 평가하세요.
자막에 없는 게임 상황이나 결과를 추측하지 말고, 실제 발언과 화면 흐름에 근거해 평가하세요.
편집본에는 해당 게임이나 플레이 상황이 본격적으로 시작되는 오프닝 구간과 마지막 결과·반전·리액션을
정리하는 엔딩 구간이 반드시 포함되어야 합니다. 단순 인사·대기·메뉴 화면은 오프닝으로 보지 말고,
실제 플레이가 시작되는 구간을 우선하세요. 엔딩은 마지막 승패·결과·정리 또는 강한 리액션이 있는 구간을 우선하세요.
반드시 JSON {\"items\":[{\"id\":number,\"score\":number,\"reason\":string}]}만 반환하세요.
score는 0부터 1000 사이입니다.""",
}


def _parse_json(value: str) -> Any:
    text = (value or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        start = min((index for index in (text.find("{"), text.find("[")) if index >= 0), default=-1)
        end = max(text.rfind("}"), text.rfind("]"))
        if start < 0 or end <= start:
            raise
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Models occasionally emit a trailing comma immediately before
            # a closing object/array even with responseMimeType=json.
            repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
            return json.loads(repaired)


class GeminiAgents:
    """Small JSON-only agents with retries and strict local validation."""

    def __init__(self, *, api_key: str | None = None, model: str | None = None, timeout: float = 120.0):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        if not self.api_key:
            raise GeminiAgentError(
                "GEMINI_API_KEY가 설정되지 않았습니다. test_yt_web/token.env에 추가하세요."
            )
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.timeout = timeout
        self.cache_dir = Path(os.getenv("GEMINI_CACHE_DIR", "media/gemini-cache")).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_request_interval = float(os.getenv("GEMINI_MIN_REQUEST_INTERVAL", "2.0"))
        self._last_request_at = 0.0
        self.url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )

    def _request_json(
        self,
        system: str,
        prompt: str,
        *,
        retries: int = 3,
        response_schema: dict[str, Any] | None = None,
        allow_model_fallback: bool = True,
    ) -> Any:
        try:
            import requests
        except ImportError as exc:
            raise GeminiAgentError("requests가 설치되지 않았습니다.") from exc

        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.15,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }
        if response_schema:
            payload["generationConfig"]["responseSchema"] = response_schema
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "model": self.model,
                    "system": system,
                    "prompt": prompt,
                    "schema": response_schema,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cache_path.unlink(missing_ok=True)
        last_error: Exception | None = None
        last_status: int | None = None
        request_prompt = prompt
        for attempt in range(retries):
            try:
                payload["contents"] = [{"role": "user", "parts": [{"text": request_prompt}]}]
                elapsed = time.monotonic() - self._last_request_at
                if elapsed < self.min_request_interval:
                    time.sleep(self.min_request_interval - elapsed)
                self._last_request_at = time.monotonic()
                response = requests.post(
                    self.url,
                    headers={
                        "x-goog-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
                last_status = response.status_code
                if response.status_code == 429 and attempt < retries - 1:
                    time.sleep(self._retry_delay(response, attempt))
                    continue
                if response.status_code >= 500 and attempt < retries - 1:
                    time.sleep(self._retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                body = response.json()
                raw = body["candidates"][0]["content"]["parts"][0]["text"]
                try:
                    parsed = _parse_json(raw)
                    cache_path.write_text(
                        json.dumps(parsed, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    return parsed
                except json.JSONDecodeError as exc:
                    last_error = exc
                    if attempt < retries - 1:
                        request_prompt = (
                            "이전 응답은 JSON 문법 오류로 사용할 수 없습니다.\n"
                            "아래의 원본 응답을 의미 변경 없이 올바른 JSON으로 고치세요.\n"
                            "마크다운, 설명, 코드펜스 없이 JSON만 반환하세요.\n\n"
                            "원본 응답:\n" + raw[:32000]
                        )
                        time.sleep(1.0)
                        continue
                    raise
            except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt == retries - 1 and last_status == 429 and allow_model_fallback:
                    fallback_result = self._retry_with_fallback_model(
                        system,
                        prompt,
                        response_schema,
                    )
                    if fallback_result is not None:
                        return fallback_result
                if attempt < retries - 1:
                    time.sleep(2.0 ** attempt)
        raise GeminiAgentError(f"Gemini JSON 응답을 받지 못했습니다: {last_error}") from last_error

    def _retry_with_fallback_model(
        self,
        system: str,
        prompt: str,
        response_schema: dict[str, Any] | None,
    ) -> Any:
        fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite").strip()
        if not fallback_model or fallback_model == self.model:
            return None
        self.model = fallback_model
        self.url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        return self._request_json(
            system,
            prompt,
            retries=2,
            response_schema=response_schema,
            allow_model_fallback=False,
        )

    def _retry_delay(self, response: Any, attempt: int) -> float:
        """Use server-provided retry timing, then exponential backoff+jitter."""

        header = response.headers.get("Retry-After") if getattr(response, "headers", None) else None
        if header:
            try:
                return min(180.0, max(2.0, float(header))) + random.uniform(0.0, 1.5)
            except ValueError:
                pass
        try:
            body = response.json()
            for detail in body.get("error", {}).get("details", []):
                retry_delay = str(detail.get("retryDelay", ""))
                match = re.fullmatch(r"(\d+(?:\.\d+)?)s", retry_delay)
                if match:
                    return min(180.0, max(2.0, float(match.group(1)))) + random.uniform(0.0, 1.5)
        except (ValueError, TypeError, AttributeError):
            pass
        return min(180.0, 4.0 * (2 ** attempt)) + random.uniform(0.0, 1.5)

    def clean_transcript(self, segments: list[dict[str, Any]]) -> dict[str, Any]:
        """Correct ASR errors and remove repeated/filler transcript segments."""

        cleaned: list[dict[str, Any]] = []
        chunk: list[dict[str, Any]] = []
        chars = 0

        def flush() -> None:
            nonlocal chunk, chars
            if not chunk:
                return
            payload = [
                {"id": index, "start": item["start"], "end": item["end"], "text": item["text"]}
                for index, item in enumerate(chunk)
            ]
            result = self._request_json(
                """당신은 한국어 방송 자막 교정 에이전트입니다.
오디오에 없는 사실을 만들지 말고, 각 id와 시간은 변경하지 마세요.
입력은 일반 문장이 아니라 YouTube 자동자막입니다. 한 문장이 다음 자막에 누적되어
반복되거나, 같은 문장이 0.01초 간격으로 잘려 여러 번 들어올 수 있습니다.

반드시 다음 규칙을 지키세요.
1. 전체 자막을 시간순으로 비교하세요. 각 자막은 앞 자막의 내용을 그대로 복사하지 말고,
   해당 시간대에 새로 말한 내용만 남기세요. 앞 자막의 접두어가 반복되면 제거하세요.
2. 앞 자막과 의미가 같은 완전 중복·부분 중복·접미어 조각이면 keep=false로 하세요.
   단, 실제로 같은 말을 다시 한 것이 아니라 새로운 정보가 추가된 경우에는 새로 추가된
   부분만 text에 남기고 keep=true로 하세요.
3. 재채기, 기침, 목을 가다듬음, 웃음 같은 대괄호 음향 태그와 의미 없는 추임새만 있는
   자막은 keep=false로 하세요.
4. 0.05초 이하의 지나치게 짧은 자막은 앞뒤 자막의 중복 조각일 가능성이 높으므로,
   새롭고 명확한 단어가 아닌 경우 keep=false로 하세요.
5. 인사·시청자 이름 호명·반복적인 방송 시작 멘트는 정보가 없으면 제거하세요.
6. 문장 내용은 오타만 자연스럽게 고치고, 숫자·고유명사·금액·종목명은 추측해서 바꾸지 마세요.
7. 모든 입력 id에 대해 정확히 한 개의 결과를 만들 필요는 없지만, 반환하는 id는 입력 id만
   사용해야 합니다. keep=false인 항목의 text는 빈 문자열로 하세요.

반드시 JSON 객체 {\"segments\":[{\"id\":number,\"text\":string,\"keep\":boolean}]}만 반환하세요.""",
                (
                    "다음 자막 묶음을 시간순으로 교정하세요. 각 id는 그대로 유지하고, 서로 겹치는 자막을\n"
                    "비교하여 반복된 부분은 제거하세요. 특히 앞 문장을 거의 그대로 포함한 다음 문장은 새로 추가된\n"
                    "말만 남기거나, 새 내용이 없으면 keep=false로 처리하세요.\n"
                )
                + json.dumps(payload, ensure_ascii=False),
                response_schema={
                    "type": "OBJECT",
                    "properties": {
                        "segments": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "id": {"type": "INTEGER"},
                                    "text": {"type": "STRING"},
                                    "keep": {"type": "BOOLEAN"},
                                },
                                "required": ["id", "text", "keep"],
                            },
                        }
                    },
                    "required": ["segments"],
                },
            )
            items = result.get("segments", []) if isinstance(result, dict) else []
            by_id = {
                int(item["id"]): item
                for item in items
                if isinstance(item, dict) and str(item.get("id", "")).isdigit()
            }
            for index, original in enumerate(chunk):
                edited = by_id.get(index, {})
                text = str(edited.get("text", original["text"])).strip()
                if text and edited.get("keep", True) is not False:
                    cleaned.append({**original, "text": text})
            chunk = []
            chars = 0

        for item in segments:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            candidate = {"start": float(item["start"]), "end": float(item["end"]), "text": text}
            if chunk and (len(chunk) >= 40 or chars + len(text) > 12000):
                flush()
            chunk.append(candidate)
            chars += len(text)
        flush()

        # The model handles semantic cleanup, but automatic captions can still
        # leave 0.01-second rolling-caption fragments or overlapping copies.
        # Apply a deterministic final pass so those artifacts cannot reach
        # clustering and highlight scoring.
        noise_tags = re.compile(
            r"\[(?:기침|목을\s*가다듬음|헛기침|웃음|한숨|숨소리|박수|환호)\]",
            re.IGNORECASE,
        )

        def normalize(value: str) -> str:
            value = noise_tags.sub(" ", value)
            return re.sub(r"\s+", " ", value).strip()

        def remove_repeated_prefix(previous: str, current: str) -> str:
            previous = normalize(previous)
            current = normalize(current)
            if previous and current.startswith(previous) and len(current) > len(previous):
                return current[len(previous):].lstrip(" ,.!?;:")
            previous_tokens = previous.split()
            current_tokens = current.split()
            # Rolling captions may repeat only the end of the previous cue at
            # the beginning of the next cue, for example:
            # "... 네. iicd" -> "네. iicd 7H 님, 안녕하세요."
            for size in range(
                min(len(previous_tokens), len(current_tokens)), 1, -1
            ):
                if previous_tokens[-size:] == current_tokens[:size]:
                    return " ".join(current_tokens[size:]).lstrip(" ,.!?;:")
            return current

        deduped: list[dict[str, Any]] = []
        for item in cleaned:
            text = normalize(item["text"])
            if not text:
                continue
            # Rolling captions often produce a second cue only 0.01 seconds
            # long. It is not useful as an independent transcript segment.
            if float(item["end"]) - float(item["start"]) <= 0.05:
                continue

            current = text
            duplicate = False
            for previous in reversed(deduped[-4:]):
                previous_text = normalize(previous["text"])
                if current == previous_text or (
                    len(current) >= 4 and current in previous_text
                ):
                    previous["end"] = max(previous["end"], item["end"])
                    duplicate = True
                    break
                stripped = remove_repeated_prefix(previous_text, current)
                if stripped != current:
                    current = stripped
                    if not current:
                        previous["end"] = max(previous["end"], item["end"])
                        duplicate = True
                        break

            if duplicate or not current:
                continue
            deduped.append({**item, "text": current})
        return {"segments": deduped, "removed_count": max(0, len(segments) - len(deduped))}

    def compress_transcript(self, segments: list[dict[str, Any]]) -> dict[str, Any]:
        """Create section summaries and a final story/key-point compression."""

        chunks: list[str] = []
        current: list[str] = []
        chars = 0
        for item in segments:
            line = f"[{item['start']:.1f}-{item['end']:.1f}] {item['text']}"
            if current and chars + len(line) > 14000:
                chunks.append("\n".join(current))
                current, chars = [], 0
            current.append(line)
            chars += len(line)
        if current:
            chunks.append("\n".join(current))

        section_summaries = []
        for section in chunks:
            result = self._request_json(
                """당신은 긴 한국어 영상의 핵심 내용을 압축하는 에이전트입니다.
반복, 인사, 침묵, 주변 대화는 제외하고 주장·근거·결론·사건 흐름을 보존하세요.
JSON {\"summary\":string,\"key_points\":[string]}만 반환하세요.""",
                "다음 시간표시 자막을 한 개의 논리적 구간으로 요약하세요.\n" + section,
                response_schema={
                    "type": "OBJECT",
                    "properties": {
                        "summary": {"type": "STRING"},
                        "key_points": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["summary", "key_points"],
                },
            )
            if isinstance(result, dict):
                section_summaries.append(result)

        compact_input = json.dumps(section_summaries, ensure_ascii=False)
        if len(compact_input) > 24000:
            compact_input = compact_input[:24000]
        final = self._request_json(
            """당신은 영상 편집용 최종 요약 에이전트입니다.
전체 흐름을 짧고 자연스럽게 설명하고, 시청자가 반드시 알아야 할 핵심 내용을 우선하세요.
사실을 추가하지 말고 JSON {\"summary\":string,\"key_points\":[string],\"chapters\":[{\"title\":string,\"summary\":string}]}만 반환하세요.""",
            "다음은 여러 자막 구간의 중간 요약입니다. 중복을 합치고 최종 요약을 작성하세요.\n"
            + compact_input,
            response_schema={
                "type": "OBJECT",
                "properties": {
                    "summary": {"type": "STRING"},
                    "key_points": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "chapters": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "title": {"type": "STRING"},
                                "summary": {"type": "STRING"},
                            },
                            "required": ["title", "summary"],
                        },
                    },
                },
                "required": ["summary", "key_points", "chapters"],
            },
        )
        if not isinstance(final, dict):
            raise GeminiAgentError("최종 요약 응답 형식이 올바르지 않습니다.")
        final["section_summaries"] = section_summaries
        return final

    def score_clusters(
        self,
        clusters: list[dict[str, Any]],
        summary: dict[str, Any],
        *,
        genre: str = "ai_news",
        batch_size: int = 20,
    ) -> list[dict[str, Any]]:
        """Rank transcript candidates using the compressed story context."""

        context = json.dumps(
            {"summary": summary.get("summary", ""), "key_points": summary.get("key_points", [])},
            ensure_ascii=False,
        )[:10000]
        scored: list[dict[str, Any]] = []
        for offset in range(0, len(clusters), batch_size):
            batch = clusters[offset : offset + batch_size]
            payload = [
                {"id": offset + index, "start": item["start"], "end": item["end"], "text": item["text"], "chat_score": item.get("chat_score", 0)}
                for index, item in enumerate(batch)
            ]
            result = self._request_json(
                GENRE_HIGHLIGHT_SYSTEM_PROMPTS.get(
                    genre, GENRE_HIGHLIGHT_SYSTEM_PROMPTS["ai_news"]
                ),
                f"장르: {GENRE_LABELS.get(genre, genre)}\n"
                f"전체 요약:\n{context}\n\n평가 대상:\n"
                + json.dumps(payload, ensure_ascii=False),
                response_schema={
                    "type": "OBJECT",
                    "properties": {
                        "items": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "id": {"type": "INTEGER"},
                                    "score": {"type": "NUMBER"},
                                    "reason": {"type": "STRING"},
                                },
                                "required": ["id", "score", "reason"],
                            },
                        }
                    },
                    "required": ["items"],
                },
            )
            items = result.get("items", []) if isinstance(result, dict) else []
            by_id = {}
            for item in items:
                try:
                    by_id[int(item["id"])] = {
                        "llm_score": max(0.0, min(1000.0, float(item.get("score", 0)))),
                        "reason": str(item.get("reason", "")),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
            for index, item in enumerate(batch):
                scored.append({**item, **by_id.get(offset + index, {"llm_score": 0.0, "reason": "LLM 평가 누락"})})
        return scored

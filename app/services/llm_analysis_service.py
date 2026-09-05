"""공급자와 무관한 JSON 기반 영상 구조화·하이라이트 분석 서비스."""
from __future__ import annotations
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable
from app.services.llm_gateway import LLMGateway, LLMGatewayError

class LLMAnalysisError(RuntimeError):
    """LLM 응답이 분석 계약을 지키지 않았을 때 발생한다."""

GENRE_GUIDES = {
    "ai_news": "AI 뉴스: 높은 점수는 실제 새 발표·정책·제품·연구·사건, 모델명·기관·수치, 원인→영향→결론 연결이다. 낮은 점수는 광고·반복·침묵·근거 없는 전망이다. 사실과 진행자 의견·추측을 구분한다.",
    "stock": "주식·증시: 높은 점수는 기업·산업·거시 사건, 실적·수치, 촉매·위험, 근거→결론 연결이다. 낮은 점수는 광고·종목 나열·반복·침묵·무근거 확신이다. 사실과 진행자 의견·투자 추측을 구분한다.",
    "game": "게임: 높은 점수는 전략의 전제→실행→결과, 패치·시스템 변화, 전문 용어, 중요한 판단·전환점·반전이다. 낮은 점수는 광고·반복·침묵·근거 없는 과장이다. 사실과 플레이 감상·추측을 구분한다.",
}
CHAPTER_SYSTEM = '''역할: 낮은 오류 허용도의 영상 편집용 스크립트 구조화기.
목표: 입력 JSONL 전체를 시간순으로 완전 분할한 챕터 JSON만 반환한다.
입력 형식: 각 줄은 {"id": 정수, "text": 문자열}이며 id는 시간순이다.
판단 기준: 실제 주제 전환, 논점 변화, 사건 흐름, 결론을 기준으로만 나눈다. 균등 분할, 단순 시간 기준 분할, 임의 챕터 수는 금지한다.
사실성 금지: 입력 text 밖의 사실·시간·원인·결론·id를 만들지 않는다.
범위·검증 규칙: 제공된 JSONL id만 사용한다. 첫 항목 start_id는 첫 입력 id, 다음 항목 start_id는 바로 앞 end_id+1, 마지막 end_id는 마지막 입력 id다. 빈틈·겹침·중복·누락·역순은 금지한다.
summary: 이 챕터에서 실제로 말한 내용을 160자 이내로 요약한다. 판단할 수 없으면 빈 문자열을 쓴다.
score: 이 챕터가 최종 편집에서 갖는 중요도를 0~1000 정수로 평가한다. 900~1000은 영상의 핵심 결론·사건·반전, 700~899는 핵심 맥락·근거·시작 또는 종료 인사, 400~699는 유용한 보조 내용, 0~399는 광고·반복·침묵·무근거 주장이다. 관습적인 앵커값에 고정하지 말고 챕터 간 상대적 차이를 일의 자리까지 세밀하게 반영한다.
출력 규칙: JSON 외 텍스트, Markdown, 코드펜스, 설명, 주석을 절대 쓰지 말고 아래 JSON 객체만 반환한다.
출력 형식: {"chapters":[{"start_id":number,"end_id":number,"summary":string,"score":number}]}'''
SECTION_SYSTEM = '''역할: 영상 편집용 챕터 내부 최소 의미 단위 분할기.
목표: 한 주장·근거·사건·설명이 끝나는 최소 연속 범위로 입력 전체를 분할한다.
입력 형식: 각 줄은 {"id": 정수, "text": 문자열} JSONL이다.
판단·금지: 문장마다 기계적으로 쪼개기, 서로 다른 논점을 한 범위로 과도하게 합치기, 입력 밖 사실·시간·id 추가를 금지한다.
범위·검증 규칙: 제공된 id만 사용한다. 첫 항목 start_id는 첫 입력 id, 다음 항목 start_id는 바로 앞 end_id+1, 마지막 end_id는 마지막 입력 id다. 빈틈·겹침·중복·누락·역순은 금지한다.
출력 규칙: JSON 외 텍스트, Markdown, 코드펜스, 설명, 주석을 절대 쓰지 말고 아래 JSON 객체만 반환한다.
출력 형식: {"sections":[{"start_id":number,"end_id":number}]}'''
CHAPTER_RESPONSE_SCHEMA={"type":"object","additionalProperties":False,"required":["chapters"],"properties":{"chapters":{"type":"array","items":{"type":"object","additionalProperties":False,"required":["start_id","end_id","summary","score"],"properties":{"start_id":{"type":"integer"},"end_id":{"type":"integer"},"summary":{"type":"string"},"score":{"type":"integer","minimum":0,"maximum":1000}}}}}}
SECTION_RESPONSE_SCHEMA={"type":"object","additionalProperties":False,"required":["sections"],"properties":{"sections":{"type":"array","items":{"type":"object","additionalProperties":False,"required":["start_id","end_id"],"properties":{"start_id":{"type":"integer"},"end_id":{"type":"integer"}}}}}}
SCORE_RESPONSE_SCHEMA={"type":"object","additionalProperties":False,"required":["items"],"properties":{"items":{"type":"array","items":{"type":"object","additionalProperties":False,"required":["id","score"],"properties":{"id":{"type":"string"},"score":{"type":"integer","minimum":0,"maximum":1000}}}}}}

class LLMAnalysisService:
    def __init__(self, *, provider: str="deepseek", server_access_token: str|None=None, **_: Any):
        try: self.gateway=LLMGateway(provider, server_access_token=server_access_token)
        except LLMGatewayError as exc: raise LLMAnalysisError(str(exc)) from exc
        self._max_parallel_requests = self.gateway.max_parallel_requests
        self._minimum_request_interval_seconds = self.gateway.minimum_request_interval_seconds
        self._request_limit_lock = threading.Lock()
        self._last_request_started_at = 0.0
    def _wait_for_request_slot(self, cancel_callback: Callable[[],None]|None) -> None:
        interval = getattr(self, "_minimum_request_interval_seconds", 0.0)
        if interval <= 0:
            return
        with self._request_limit_lock:
            remaining = interval - (time.monotonic() - self._last_request_started_at)
            while remaining > 0:
                if cancel_callback: cancel_callback()
                time.sleep(min(0.25, remaining))
                remaining = interval - (time.monotonic() - self._last_request_started_at)
            if cancel_callback: cancel_callback()
            self._last_request_started_at = time.monotonic()
    def _request_json(self, system: str, prompt: str, *, response_schema: dict[str,Any]|None=None, validator: Callable[[Any],Any]|None=None, cancel_callback: Callable[[],None]|None=None) -> Any:
        last_error: Exception|None=None
        for attempt in range(10):
            if cancel_callback: cancel_callback()
            self._wait_for_request_slot(cancel_callback)
            rule="" if attempt==0 else "\n직전 응답은 JSON 문법 또는 배열 길이·ID 범위 계약을 지키지 못했습니다. 설명하지 말고 완결된 JSON 객체 하나만 반환하세요. 입력 순서와 길이, 문자열·쉼표·대괄호·중괄호를 확인하세요."
            try:
                value=json.loads(self.gateway.request_json(system+rule,prompt,response_schema=response_schema))
                value = validator(value) if validator else value
                if cancel_callback: cancel_callback()
                return value
            except LLMGatewayError as exc: raise LLMAnalysisError(f"구조화 JSON 요청에 실패했습니다: {exc}") from exc
            except (json.JSONDecodeError,LLMAnalysisError) as exc: last_error=exc
        raise LLMAnalysisError("LLM이 열 번 연속 JSON 문법 또는 응답 계약을 지키지 않았습니다.") from last_error
    @staticmethod
    def _validated_ranges(raw: Any, ids: list[int], *, key: str, require_chapter_fields: bool) -> list[dict[str,Any]]:
        if not isinstance(raw,dict) or set(raw) != {key}:
            raise LLMAnalysisError(f"{key} 응답 객체 형식이 올바르지 않습니다.")
        values=raw.get(key)
        if not isinstance(values,list) or not values: raise LLMAnalysisError(f"{key} 응답 형식이 올바르지 않습니다.")
        expected=ids[0]; known=set(ids); result=[]
        for item in values:
            if not isinstance(item,dict): raise LLMAnalysisError(f"{key} 항목 형식이 올바르지 않습니다.")
            expected_fields={"start_id","end_id","summary","score"} if require_chapter_fields else {"start_id","end_id"}
            if set(item) != expected_fields: raise LLMAnalysisError(f"{key} 항목 필드가 응답 계약과 다릅니다.")
            start,end=item.get("start_id"),item.get("end_id")
            if type(start) is not int or type(end) is not int or start not in known or end not in known or start>end: raise LLMAnalysisError(f"{key} ID 범위가 올바르지 않습니다.")
            if start!=expected: raise LLMAnalysisError(f"{key} ID가 순서대로 전체 입력을 덮지 않습니다.")
            if require_chapter_fields and (not isinstance(item.get("summary"),str) or type(item.get("score")) is not int or not 0 <= item["score"] <= 1000): raise LLMAnalysisError(f"{key}의 summary 또는 score 형식이 올바르지 않습니다.")
            expected=end+1; result.append(item)
        if expected!=ids[-1]+1: raise LLMAnalysisError(f"{key}가 입력 마지막 ID까지 덮지 않습니다.")
        return result
    @staticmethod
    def _jsonl(rows: list[dict[str,Any]]) -> str: return "\n".join(json.dumps(row,ensure_ascii=False,separators=(",",":")) for row in rows)
    def structure_transcript(self, segments: list[dict[str,Any]], *, progress_callback: Callable[[int,int,str],None]|None=None, cancel_callback: Callable[[],None]|None=None) -> dict[str,Any]:
        rows=[{"id":int(item["id"]),"text":str(item["text"])} for item in segments]
        if not rows: raise LLMAnalysisError("구조화할 스크립트가 없습니다.")
        ids=[row["id"] for row in rows]
        if ids!=list(range(ids[0],ids[-1]+1)): raise LLMAnalysisError("입력 스크립트 ID가 연속적이지 않습니다.")
        if progress_callback: progress_callback(0, 1, "챕터 분할 요청")
        chapters=self._request_json(CHAPTER_SYSTEM,self._jsonl(rows),response_schema=CHAPTER_RESPONSE_SCHEMA,validator=lambda raw:self._validated_ranges(raw,ids,key="chapters",require_chapter_fields=True),cancel_callback=cancel_callback)
        if progress_callback: progress_callback(1, 1, "챕터 분할 완료")
        def split(index: int, chapter: dict[str,Any]) -> tuple[int,list[dict[str,Any]]]:
            if cancel_callback: cancel_callback()
            chapter_rows=[row for row in rows if chapter["start_id"]<=row["id"]<=chapter["end_id"]]
            values=self._request_json(SECTION_SYSTEM,self._jsonl(chapter_rows),response_schema=SECTION_RESPONSE_SCHEMA,validator=lambda raw:self._validated_ranges(raw,[row["id"] for row in chapter_rows],key="sections",require_chapter_fields=False),cancel_callback=cancel_callback)
            return index, values
        indexed=[]
        with ThreadPoolExecutor(max_workers=min(getattr(self, "_max_parallel_requests", 100),len(chapters))) as executor:
            futures=[executor.submit(split,index,chapter) for index,chapter in enumerate(chapters)]
            for completed,future in enumerate(as_completed(futures),1):
                if cancel_callback: cancel_callback()
                indexed.append(future.result())
                if progress_callback: progress_callback(completed,len(futures),"챕터별 섹션 분할")
        sections=[]
        for index,values in sorted(indexed): sections.extend({"chapter_index":index,**section} for section in values)
        return {"chapters":chapters,"sections":sections}
    def score_sections(self, sections: list[dict[str,Any]], *, genre: str="ai_news", progress_callback: Callable[[int,int,str],None]|None=None, cancel_callback: Callable[[],None]|None=None) -> list[dict[str,Any]]:
        groups: dict[str,list[dict[str,Any]]]={}
        for section in sections: groups.setdefault(str(section.get("chapter_id","")),[]).append(section)
        system=f'''역할: {genre} 영상의 섹션 단위 편집 하이라이트 평가자.
목표: 한 챕터의 각 입력 섹션 중요도를 입력 순서대로 0~1000 정수로 평가한다.
{GENRE_GUIDES.get(genre,GENRE_GUIDES["ai_news"])}
점수 기준: 900~1000은 핵심 사실·결론·반전 또는 반드시 필요한 근거, 700~899는 맥락·원인·결과를 보존하는 중요 설명과 시작·종료 인사, 400~699는 유용하지만 생략 가능한 보조 내용, 0~399는 광고·반복·침묵·추측·무근거 주장이다. 관습적인 앵커 숫자에 고정하지 말고 섹션 간 상대적 중요도를 비교해 일의 자리까지 세밀한 정수를 사용한다.
시작 인사·영상 주제 소개·종료 인사는 높은 점수를 준다. 입력 밖 사실·시간·원인·결론을 만들지 않는다.
챕터 요약: 아래 입력에는 이 섹션들이 속한 챕터의 입력 기반 summary가 함께 제공된다. 이 요약을 기준으로 챕터 안에서 핵심 주장·근거·결론을 우선 판별하되, 요약이나 섹션 본문에 없는 사실은 만들지 않는다.
입력 형식: {{"chapter_summary":string,"sections":[{{"id":섹션 ID,"text":섹션 본문}}]}}이다. 제공된 id를 바꾸거나 새로 만들거나 누락하지 말고, 순서를 바꾸거나 섹션을 합치거나 나누지 않는다.
검증: items 배열은 입력과 길이가 같고 각 id를 중복·누락 없이 정확히 한 번 사용한다. score는 0~1000 정수다.
출력: JSON 외 텍스트, Markdown, 코드펜스, 설명을 금지한다. {{"items":[{{"id":string,"score":number}}]}}만 반환한다. 이유·다른 필드는 반환하지 않는다.'''
        def score(chapter_sections: list[dict[str,Any]]) -> list[dict[str,Any]]:
            if cancel_callback: cancel_callback()
            payload=[{"id":str(section.get("section_id") or section.get("segment_id") or ""),"text":str(section.get("text",""))} for section in chapter_sections]
            raw=self._request_json(system,json.dumps({"chapter_summary":str(chapter_sections[0].get("chapter_summary", "")),"sections":payload},ensure_ascii=False),response_schema=SCORE_RESPONSE_SCHEMA,cancel_callback=cancel_callback)
            if not isinstance(raw,dict) or set(raw) != {"items"}: raise LLMAnalysisError("섹션 중요도 응답 객체 형식이 올바르지 않습니다.")
            items=raw.get("items")
            if not isinstance(items,list) or len(items)!=len(chapter_sections): raise LLMAnalysisError("섹션 중요도 응답이 입력과 일치하지 않습니다.")
            if any(not isinstance(item,dict) or set(item)!={"id","score"} for item in items): raise LLMAnalysisError("섹션 중요도 항목 필드가 응답 계약과 다릅니다.")
            scores={item.get("id"):item.get("score") for item in items if isinstance(item,dict)}
            expected=[item["id"] for item in payload]
            if len(scores)!=len(expected) or set(scores)!=set(expected): raise LLMAnalysisError("섹션 중요도 ID가 입력과 일치하지 않습니다.")
            if any(type(scores[item_id]) is not int or not 0<=scores[item_id]<=1000 for item_id in expected): raise LLMAnalysisError("섹션 중요도 점수 형식이 올바르지 않습니다.")
            return [{**section,"llm_score":float(scores[item["id"]])} for section,item in zip(chapter_sections,payload)]
        result=[]
        with ThreadPoolExecutor(max_workers=min(getattr(self, "_max_parallel_requests", 100),len(groups))) as executor:
            futures=[executor.submit(score, group) for group in groups.values()]
            for completed,future in enumerate(as_completed(futures),1):
                if cancel_callback: cancel_callback()
                result.extend(future.result())
                if progress_callback: progress_callback(completed,len(futures),"챕터별 섹션 중요도 평가")
        return sorted(result,key=lambda item:(str(item.get("chapter_id","")),float(item.get("start",0))))

import json

from app.services.llm_analysis_service import CHAPTER_SYSTEM, GENRE_GUIDES, SECTION_SYSTEM, LLMAnalysisError, LLMAnalysisService


def test_prompts_define_summary_and_precise_score_contract():
    prompt = f"{CHAPTER_SYSTEM}\n{SECTION_SYSTEM}\n" + "\n".join(GENRE_GUIDES.values())
    assert "Gemini" not in prompt and "DeepSeek" not in prompt
    assert "summary" in CHAPTER_SYSTEM and "title" not in CHAPTER_SYSTEM
    assert "summary" not in SECTION_SYSTEM and "reason" not in SECTION_SYSTEM
    assert "균등 분할" in CHAPTER_SYSTEM and "문장마다 기계적으로" in SECTION_SYSTEM


def test_structure_requires_contiguous_ids_and_summaries(monkeypatch):
    agent = object.__new__(LLMAnalysisService)
    responses = iter([
        {"chapters": [{"start_id": 0, "end_id": 1, "summary": "주제 요약", "score": 812}]},
        {"sections": [{"start_id": 0, "end_id": 1}]},
    ])
    monkeypatch.setattr(agent, "_request_json", lambda *args, **kwargs: kwargs["validator"](next(responses)))
    result = agent.structure_transcript([{"id": 0, "text": "가"}, {"id": 1, "text": "나"}])
    assert result["chapters"][0]["summary"] == "주제 요약"
    assert result["sections"][0] == {"chapter_index": 0, "start_id": 0, "end_id": 1}


def test_score_sends_one_section_id_text_array_and_chapter_summary_per_chapter(monkeypatch):
    agent = object.__new__(LLMAnalysisService)
    prompts = []
    scores = iter([{"items": [{"id": "a-1", "score": 721}, {"id": "a-2", "score": 814}]}, {"items": [{"id": "b-1", "score": 903}]}])
    def request(_system, prompt, **_kwargs):
        prompts.append(json.loads(prompt))
        return next(scores)
    monkeypatch.setattr(agent, "_request_json", request)
    result = agent.score_sections([
        {"chapter_id": "a", "chapter_summary": "첫 챕터 요약", "section_id": "a-1", "text": "첫 섹션"}, {"chapter_id": "a", "chapter_summary": "첫 챕터 요약", "section_id": "a-2", "text": "둘째 섹션"}, {"chapter_id": "b", "chapter_summary": "둘째 챕터 요약", "section_id": "b-1", "text": "다른 챕터"},
    ])
    assert prompts == [{"chapter_summary": "첫 챕터 요약", "sections": [{"id": "a-1", "text": "첫 섹션"}, {"id": "a-2", "text": "둘째 섹션"}]}, {"chapter_summary": "둘째 챕터 요약", "sections": [{"id": "b-1", "text": "다른 챕터"}]}]
    assert [item["llm_score"] for item in result] == [721.0, 814.0, 903.0]
    assert all("reason" not in item for item in result)


def test_score_rejects_wrong_score_count(monkeypatch):
    agent = object.__new__(LLMAnalysisService)
    monkeypatch.setattr(agent, "_request_json", lambda *_args, **_kwargs: {"items": [{"id": "a-1", "score": 900}]})
    try:
        agent.score_sections([{"chapter_id": "a", "section_id": "a-1", "text": "첫"}, {"chapter_id": "a", "section_id": "a-2", "text": "둘째"}])
    except LLMAnalysisError as exc:
        assert "일치" in str(exc)
    else:
        raise AssertionError("wrong score count must be rejected")


def test_request_json_retries_after_range_error():
    class Gateway:
        def __init__(self): self.calls = 0
        def request_json(self, *_args, **_kwargs):
            self.calls += 1
            return '{"sections":[{"start_id":1,"end_id":2}]}' if self.calls == 1 else '{"sections":[{"start_id":0,"end_id":2}]}'
    agent = object.__new__(LLMAnalysisService); agent.gateway = Gateway()
    result = agent._request_json(SECTION_SYSTEM, "입력", validator=lambda raw: agent._validated_ranges(raw, [0, 1, 2], key="sections", require_chapter_fields=False))
    assert result[0]["start_id"] == 0 and agent.gateway.calls == 2


def test_request_json_stops_after_ten_invalid_responses():
    class Gateway:
        def __init__(self): self.calls = 0
        def request_json(self, *_args, **_kwargs):
            self.calls += 1
            return '{"broken":'
    agent = object.__new__(LLMAnalysisService); agent.gateway = Gateway()
    try:
        agent._request_json("계약", "입력")
    except LLMAnalysisError as exc:
        assert "열 번" in str(exc)
    else:
        raise AssertionError("ten invalid responses must fail")
    assert agent.gateway.calls == 10


def test_chapter_score_requires_an_integer_in_range():
    raw = {"chapters": [{"start_id": 0, "end_id": 1, "summary": "요약", "score": 1001}]}

    try:
        LLMAnalysisService._validated_ranges(raw, [0, 1], key="chapters", require_chapter_fields=True)
    except LLMAnalysisError as exc:
        assert "score" in str(exc)
    else:
        raise AssertionError("out-of-range chapter score must be rejected")


def test_structure_rejects_extra_response_fields():
    raw = {"chapters": [{"start_id": 0, "end_id": 0, "summary": "요약", "score": 500, "reason": "금지"}]}

    try:
        LLMAnalysisService._validated_ranges(raw, [0], key="chapters", require_chapter_fields=True)
    except LLMAnalysisError as exc:
        assert "필드" in str(exc)
    else:
        raise AssertionError("extra chapter fields must be rejected")


def test_request_json_checks_cancellation_before_submitting_a_request():
    class Gateway:
        def request_json(self, *_args, **_kwargs):
            raise AssertionError("cancelled work must not submit an LLM request")

    agent = object.__new__(LLMAnalysisService)
    agent.gateway = Gateway()

    try:
        agent._request_json("계약", "입력", cancel_callback=lambda: (_ for _ in ()).throw(LLMAnalysisError("취소됨")))
    except LLMAnalysisError as exc:
        assert str(exc) == "취소됨"
    else:
        raise AssertionError("cancellation must stop the request")


def test_request_json_discards_a_completed_request_when_it_was_cancelled(monkeypatch):
    class Gateway:
        def request_json(self, *_args, **_kwargs):
            return '{"sections":[{"start_id":0,"end_id":0}]}'

    agent = object.__new__(LLMAnalysisService)
    agent.gateway = Gateway()
    checks = 0

    def cancel_after_request():
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise LLMAnalysisError("취소됨")

    try:
        agent._request_json(SECTION_SYSTEM, "입력", cancel_callback=cancel_after_request)
    except LLMAnalysisError as exc:
        assert str(exc) == "취소됨"
    else:
        raise AssertionError("completed response must be discarded after cancellation")


def test_parallel_section_work_never_requests_more_than_ten_workers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor as RealExecutor
    import app.services.llm_analysis_service as service_module

    requested_workers = []

    class RecordingExecutor(RealExecutor):
        def __init__(self, *args, **kwargs):
            requested_workers.append(kwargs.get("max_workers", args[0] if args else None))
            super().__init__(*args, **kwargs)

    agent = object.__new__(LLMAnalysisService)
    monkeypatch.setattr(service_module, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(agent, "_request_json", lambda *_args, **_kwargs: {"items": [{"id": "section", "score": 500}]})

    agent.score_sections([
        {"chapter_id": f"chapter-{index}", "section_id": "section", "text": "본문"}
        for index in range(101)
    ])

    assert requested_workers == [100]


def test_parallel_chapter_splitting_never_requests_more_than_ten_workers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor as RealExecutor
    import app.services.llm_analysis_service as service_module

    requested_workers = []

    class RecordingExecutor(RealExecutor):
        def __init__(self, *args, **kwargs):
            requested_workers.append(kwargs.get("max_workers", args[0] if args else None))
            super().__init__(*args, **kwargs)

    agent = object.__new__(LLMAnalysisService)
    monkeypatch.setattr(service_module, "ThreadPoolExecutor", RecordingExecutor)

    def request(system, _prompt, **kwargs):
        if system == CHAPTER_SYSTEM:
            raw = {"chapters": [
                {"start_id": index, "end_id": index, "summary": str(index), "score": 500}
                for index in range(101)
            ]}
        else:
            start = int(_prompt.split('"id":')[1].split(",")[0])
            raw = {"sections": [{"start_id": start, "end_id": start}]}
        validator = kwargs.get("validator")
        return validator(raw) if validator else raw

    monkeypatch.setattr(agent, "_request_json", request)
    agent.structure_transcript([{"id": index, "text": str(index)} for index in range(101)])

    assert requested_workers == [100]

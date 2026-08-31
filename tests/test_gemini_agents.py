import json

from app.services.gemini_agents import GeminiAgents, _compact_timed_section_summaries


def test_clean_transcript_sends_id_and_text_only_and_applies_only_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_CACHE_DIR", str(tmp_path / "gemini-cache"))
    agent = GeminiAgents(api_key="test-key")
    calls = []

    def fake_request(_system, prompt, **_kwargs):
        calls.append(prompt)
        return {"segments": [{"id": 11, "text": "수정된 자막", "keep": True}, {"id": 12, "text": "", "keep": False}]}

    monkeypatch.setattr(agent, "_request_json", fake_request)
    result = agent.clean_transcript(
        [
            {"id": 10, "start": 1.0, "end": 2.0, "text": "그대로"},
            {"id": 11, "start": 3.0, "end": 4.0, "text": "오타"},
            {"id": 12, "start": 5.0, "end": 6.0, "text": "삭제"},
        ]
    )

    payload = json.loads(calls[0].split("\n")[-1])
    assert payload == [{"id": 10, "text": "그대로"}, {"id": 11, "text": "오타"}, {"id": 12, "text": "삭제"}]
    assert [item["id"] for item in result["segments"]] == [10, 11]
    assert result["segments"][1]["text"] == "수정된 자막"


def test_compress_transcript_requests_id_bounded_chapters_and_boundary_review(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_CACHE_DIR", str(tmp_path / "gemini-cache"))
    agent = GeminiAgents(api_key="test-key")
    calls = []

    def fake_request(system, prompt, *, response_schema=None, **_kwargs):
        calls.append({"system": system, "prompt": prompt, "schema": response_schema})
        if len(calls) <= 2:
            section_start = 0 if len(calls) == 1 else 1
            section_end = section_start
            return {
                "summary": f"구간 {len(calls)} 요약",
                "key_points": [f"핵심 {len(calls)}"],
                "chapters": [
                    {
                        "title": f"구간 {len(calls)}",
                        "summary": "주제 요약",
                        "start_id": section_start,
                        "end_id": section_end,
                    }
                ],
            }
        if len(calls) == 3:
            return {"boundaries": [{"left_section_id": 0, "right_section_id": 1, "merge": True}]}
        return {
            "summary": "전체 요약",
            "key_points": ["전체 핵심"],
            "chapters": [
                {
                    "title": "전체 주제",
                    "summary": "전체 주제 요약",
                    "start_id": 0,
                    "end_id": 1,
                }
            ],
        }

    monkeypatch.setattr(agent, "_request_json", fake_request)
    result = agent.compress_transcript(
        [
            {"id": 0, "start": 0.0, "end": 10.0, "text": "가" * 8000},
            {"id": 1, "start": 20.0, "end": 30.0, "text": "나" * 8000},
        ]
    )

    assert len(calls) == 4  # two sections, adjacent-boundary review, final
    final_call = calls[-1]
    final_payload = json.JSONDecoder().raw_decode(final_call["prompt"].split("\n", 1)[1])[0]
    assert [(item["start_id"], item["end_id"]) for item in final_payload] == [
        (0, 0),
        (1, 1),
    ]
    chapter_schema = final_call["schema"]["properties"]["chapters"]["items"]
    assert chapter_schema["properties"]["start_id"]["type"] == "INTEGER"
    assert chapter_schema["properties"]["end_id"]["type"] == "INTEGER"
    assert set(chapter_schema["required"]) == {"title", "summary", "start_id", "end_id"}
    assert result["chapters"][0]["start_id"] == 0
    assert result["chapters"][0]["end_id"] == 1


def test_timed_section_compaction_keeps_valid_json_and_timeline_edges():
    sections = [
        {
            "section_id": index,
            "start_id": index * 100,
            "end_id": index * 100 + 99,
            "summary": "긴 요약" * 500,
            "key_points": ["긴 핵심" * 200 for _ in range(10)],
            "chapters": [
                {
                    "title": f"주제 {chapter}",
                    "summary": "긴 챕터 요약" * 200,
                    "start_id": index * 100 + chapter * 10,
                    "end_id": index * 100 + chapter * 10 + 9,
                }
                for chapter in range(6)
            ],
        }
        for index in range(30)
    ]

    compact = _compact_timed_section_summaries(sections, max_chars=24000)
    parsed = json.loads(compact)

    assert len(compact) <= 24000
    assert len(parsed) == len(sections)
    assert parsed[0]["start_id"] == 0
    assert parsed[-1]["end_id"] == 2999
    assert parsed[-1]["chapters"][0]["title"] == "주제 0"
    assert parsed[-1]["chapters"][-1]["title"] == "주제 5"

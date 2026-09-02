from app.services.local_job_store import LocalJobStore


def test_local_job_store_separates_processing_data_from_media_files(tmp_path):
    store = LocalJobStore(tmp_path)
    store.create_or_update_state("job-1", {"job_id": "job-1", "status": "queued"})
    store.save_analysis(
        "job-1",
        plan={"source_video_path": "C:/media/source.mp4", "candidates": [{"segment_id": "s1"}]},
        raw_transcript={"segments": [{"id": 0, "text": "원문"}]},
        cleaned_transcript={"segments": [{"id": 0, "text": "정제문"}]},
        summary={"summary": "요약"},
        candidates=[{"segment_id": "s1", "start": 0, "end": 10}],
    )
    store.update_plan("job-1", {"source_video_path": "C:/media/source.mp4", "revision": 1})
    store.append_revision("job-1", {"revision": 1, "segment_ids": ["s1"]})

    assert (tmp_path / "ave-client.sqlite3").is_file()
    assert store.get_state("job-1")["status"] == "queued"
    analysis = store.get_analysis("job-1")
    assert analysis is not None
    assert analysis["cleaned_transcript"]["segments"][0]["text"] == "정제문"
    assert analysis["revisions"] == [{"revision": 1, "segment_ids": ["s1"]}]
    assert not list(tmp_path.glob("*.json"))

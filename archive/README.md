# Archived prototypes

The active product module is [`../test_yt_web`](../test_yt_web).  The folders
below are preserved as historical experiments and are not part of the active
runtime, dependency set, or deployment instructions.

| Archive | Capability absorbed by `test_yt_web` |
| --- | --- |
| `test_yt_scripter` | yt-dlp caption collection and VTT parsing |
| `test_stt_gateway` | Groq hosted-STT fallback (`transcription_service.py`) |
| `test_yt_editor` | clip selection, concatenation, and rendering via FFmpeg |
| `test_llm_scoring`, `test_llm_clustering`, `test_importance_score` | Gemini-based transcript scoring and chapter/candidate selection |
| `test_llm_gateway` | provider experiment; the production pipeline retains its structured Gemini agent because its output schema is editor-specific |
| `test_web` | early static UI prototype, superseded by `test_yt_web/static` |

Archived code is retained for reference and may have independent or outdated
dependencies. Do not install its requirements as part of the web editor.

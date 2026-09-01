from app.services.gemini_editor import GeminiEditor
from app.services.llm_gateway import LLMGateway


def test_each_provider_uses_its_lightweight_default_model(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)

    assert LLMGateway("gemini", api_key="test-key").model == "gemini-3.5-flash-lite"
    assert LLMGateway("deepseek", api_key="test-key").model == "deepseek-chat"


def test_gemini_editor_uses_the_same_default_model(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    assert GeminiEditor(api_key="test-key").model == "gemini-3.5-flash-lite"

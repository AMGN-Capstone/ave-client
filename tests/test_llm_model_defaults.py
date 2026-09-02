from app.services.llm_gateway import LLMGateway


def test_each_provider_uses_its_lightweight_default_model(monkeypatch):
    monkeypatch.setenv("AVE_SERVER_URL", "https://ave-server.example.test")

    assert LLMGateway("gemini", api_key="test-key").model == "gemini-3.5-flash-lite"
    assert LLMGateway("deepseek", api_key="test-key").model == "deepseek-chat"

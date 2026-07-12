from typing import List, Dict
from .providers.groq_client import GroqClient
from .providers.gemini_client import GeminiClient

class LLMGateway:
    def __init__(self, provider: str):
        self.provider = provider.lower()
        
        if self.provider == "groq":
            self.client = GroqClient()
        elif self.provider == "gemini":
            self.client = GeminiClient()
        else:
            raise ValueError(f"지원하지 않는 LLM 공급자입니다: {provider}")

    def request(self, messages: List[Dict[str, str]], **kwargs) -> str:
        return self.client.generate(messages, config=kwargs)

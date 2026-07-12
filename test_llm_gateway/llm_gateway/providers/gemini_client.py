import os
import requests
from typing import List, Dict, Any

class GeminiClient:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("환경 변수 'GEMINI_API_KEY'가 설정되지 않았습니다. .env 파일을 확인해주세요.")
        
        self.url = "https://generativelanguage.googleapis.com/v1beta/interactions"
        self.headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json"
        }

    def generate(self, messages: List[Dict[str, str]], config: Dict[str, Any]) -> str:
        payload = {
            "model": config.get("model"),
            "input": messages,
            "generation_config": {
                "temperature": config.get("temperature"),
                "max_output_tokens": config.get("max_tokens"),
                "top_p": config.get("top_p")
            }
        }

        try:
            response = requests.post(self.url, headers=self.headers, json=payload)
            response.raise_for_status()
            
            result = response.json()
            return result["steps"][-1]["content"][0]["text"]
            
        except requests.exceptions.RequestException as e:
            return f"Gemini API 요청 중 오류 발생: {e}"
        except (KeyError, IndexError):
            return "Gemini API 응답 형식이 올바르지 않습니다."

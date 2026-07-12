import os
import requests
from typing import List, Dict, Any

class DeepSeekClient:
    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("환경 변수 'DEEPSEEK_API_KEY'가 설정되지 않았습니다. .env 파일을 확인해주세요.")
        
        self.url = "https://api.deepseek.com/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def generate(self, messages: List[Dict[str, str]], config: Dict[str, Any]) -> str:
        payload = {
            "model": config.get("model"),
            "messages": messages,
            "temperature": config.get("temperature"),
            "max_tokens": config.get("max_tokens"),
            "top_p": config.get("top_p"),
            "stream": False
        }

        try:
            response = requests.post(self.url, headers=self.headers, json=payload)
            response.raise_for_status()
            
            result = response.json()
            return result["choices"][0]["message"]["content"]
            
        except requests.exceptions.RequestException as e:
            return f"DeepSeek API 요청 중 오류 발생: {e}"
        except KeyError:
            return "API 응답 형식이 올바르지 않습니다."

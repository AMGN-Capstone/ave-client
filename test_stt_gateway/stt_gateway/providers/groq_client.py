import os
import requests
from typing import Dict, Any

class GroqClient:
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("환경 변수 'GROQ_API_KEY'가 설정되지 않았습니다. .env 파일을 확인해주세요.")

        self.url = "https://api.groq.com/openai/v1/audio/transcriptions"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

    def transcribe(self, file_path: str, config: Dict[str, Any]) -> dict:
        data = {
            "model": config.get("model", "whisper-large-v3-turbo"),
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment"
        }
        
        if "language" in config and config["language"]:
            data["language"] = config["language"]

        try:
            with open(file_path, 'rb') as f:
                files = {
                    "file": (os.path.basename(file_path), f, "audio/mpeg")
                }

                response = requests.post(self.url, headers=self.headers, data=data, files=files)
                response.raise_for_status()

                return response.json()

        except requests.exceptions.RequestException as e:
            return {"error": f"Groq STT API 요청 중 오류 발생: {e}"}
        except Exception as e:
            return {"error": f"알 수 없는 오류 발생: {e}"}

"""Provider-neutral LLM gateway used by the editing agents."""

from __future__ import annotations

from typing import Any

import requests

from app.config import get_ave_server_url

SUPPORTED_LLM_PROVIDERS = ("gemini", "deepseek")


class LLMGatewayError(RuntimeError):
    pass


class LLMGateway:
    """Archive gateway contract, extended for this app's JSON-only agents."""

    def __init__(self, provider: str = "gemini", *, api_key: str | None = None, model: str | None = None, timeout: float = 120.0, server_access_token: str | None = None):
        self.provider = provider.lower().strip()
        if self.provider not in SUPPORTED_LLM_PROVIDERS:
            raise LLMGatewayError(f"지원하지 않는 LLM 공급자입니다: {provider}")
        self.server_url = get_ave_server_url()
        self.server_access_token = server_access_token or ""
        del api_key
        self.api_key = ""
        if not self.server_url.startswith("https://"):
            raise LLMGatewayError("AVE_SERVER_URL에 서버 HTTPS 주소를 설정하세요.")
        defaults = {"gemini": "gemini-3.5-flash-lite", "deepseek": "deepseek-chat"}
        self.model = (model or defaults[self.provider]).strip()
        self.timeout = timeout
        provider_limits = {"gemini": 24_000, "deepseek": 12_000}
        self.max_input_chars = provider_limits[self.provider]

    def request_json(self, system: str, prompt: str, *, response_schema: dict[str, Any] | None = None) -> str:
        if self.server_url.startswith("https://"):
            if not self.server_access_token:
                raise LLMGatewayError("서버 LLM 호출에는 로그인 토큰이 필요합니다.")
            try:
                response = requests.post(f"{self.server_url}/api/llm/generate", headers={"Authorization": self.server_access_token, "Content-Type": "application/json"}, json={"provider": self.provider, "model": self.model, "system": system, "prompt": prompt, "response_schema": response_schema}, timeout=self.timeout)
                response.raise_for_status()
                value = response.json().get("text")
            except (requests.RequestException, ValueError, AttributeError) as exc:
                raise LLMGatewayError("AVE 서버 LLM 호출에 실패했습니다.") from exc
            if not isinstance(value, str):
                raise LLMGatewayError("AVE 서버 LLM 응답 형식이 올바르지 않습니다.")
            return value
        if self.provider == "gemini":
            config: dict[str, Any] = {"temperature": 0.15, "maxOutputTokens": 8192, "responseMimeType": "application/json"}
            if response_schema:
                config["responseSchema"] = response_schema
            raise LLMGatewayError("LLM 공급자 직접 호출은 지원하지 않습니다.")
            response.raise_for_status()
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
        raise LLMGatewayError("LLM 공급자 직접 호출은 지원하지 않습니다.")
        messages = [
            {"role": "system", "content": system + "\n설명이나 코드펜스 없이 유효한 JSON만 반환하세요."},
            {"role": "user", "content": prompt},
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.15,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        payload["max_tokens"] = 8192
        response = requests.post(url, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

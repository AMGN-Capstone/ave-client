"""Provider-neutral LLM gateway used by the editing agents."""

from __future__ import annotations

import os
from typing import Any

import requests

SUPPORTED_LLM_PROVIDERS = ("gemini", "deepseek")


class LLMGatewayError(RuntimeError):
    pass


class LLMGateway:
    """Archive gateway contract, extended for this app's JSON-only agents."""

    def __init__(self, provider: str = "gemini", *, api_key: str | None = None, model: str | None = None, timeout: float = 120.0):
        self.provider = provider.lower().strip()
        if self.provider not in SUPPORTED_LLM_PROVIDERS:
            raise LLMGatewayError(f"지원하지 않는 LLM 공급자입니다: {provider}")
        key_name = f"{self.provider.upper()}_API_KEY"
        self.api_key = (api_key or os.getenv(key_name, "")).strip()
        if not self.api_key:
            raise LLMGatewayError(f"{key_name}가 설정되지 않았습니다. token.env에 추가하세요.")
        defaults = {"gemini": os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"), "deepseek": os.getenv("DEEPSEEK_MODEL", "deepseek-chat")}
        self.model = (model or defaults[self.provider]).strip()
        self.timeout = timeout
        provider_limits = {"gemini": 24_000, "deepseek": 12_000}
        configured_limit = os.getenv(
            f"{self.provider.upper()}_MAX_INPUT_CHARS",
            os.getenv("LLM_MAX_INPUT_CHARS", ""),
        ).strip()
        try:
            self.max_input_chars = max(1_000, int(configured_limit)) if configured_limit else provider_limits[self.provider]
        except ValueError:
            self.max_input_chars = provider_limits[self.provider]

    def request_json(self, system: str, prompt: str, *, response_schema: dict[str, Any] | None = None) -> str:
        if self.provider == "gemini":
            config: dict[str, Any] = {"temperature": 0.15, "maxOutputTokens": 8192, "responseMimeType": "application/json"}
            if response_schema:
                config["responseSchema"] = response_schema
            response = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent", headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"}, json={"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}, timeout=self.timeout)
            response.raise_for_status()
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
        url = "https://api.deepseek.com/chat/completions"
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

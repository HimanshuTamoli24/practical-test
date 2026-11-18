"""Minimal async Groq (OpenAI-compatible) chat client used for the analysis."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx


class LLMError(RuntimeError):
    """Raised for configuration, transport or response-format problems."""


class _ModelUnavailable(Exception):
    """Internal signal: retry with the fallback model."""


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    response_ms: int


_RETRY_STATUSES = {429, 500, 502, 503, 504}
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _parse_json_content(content: str) -> dict[str, Any]:
    cleaned = _FENCE_RE.sub("", content.strip()).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("The LLM did not return valid JSON.") from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            raise LLMError("The LLM did not return valid JSON.") from None
    if not isinstance(parsed, dict):
        raise LLMError("The LLM returned JSON that is not an object.")
    return parsed


class GroqClient:
    """Small wrapper around Groq's ``/chat/completions`` endpoint."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        fallback_model: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model if fallback_model != model else None
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _payload(self, model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_completion_tokens": 4096,
            "response_format": {"type": "json_object"},
        }
        # GPT-OSS models support a reasoning budget; "low" keeps latency down
        # for this extraction-style task.
        if "gpt-oss" in model:
            payload["reasoning_effort"] = "low"
        return payload

    async def complete_json(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if not self.api_key:
            raise LLMError(
                "GROQ_API_KEY is not configured. Copy .env.example to .env and add your key."
            )

        models = [self.model]
        if self.fallback_model:
            models.append(self.fallback_model)

        last_error: Exception | None = None
        for model in models:
            try:
                return await self._request(model, system_prompt, user_prompt)
            except _ModelUnavailable as exc:
                last_error = exc
                continue

        raise LLMError(f"No Groq model could produce a response: {last_error}")

    async def _request(self, model: str, system_prompt: str, user_prompt: str) -> LLMResult:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._payload(model, system_prompt, user_prompt)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(3):
                started = time.perf_counter()
                try:
                    response = await client.post(url, headers=headers, json=payload)
                except httpx.HTTPError as exc:
                    if attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    raise LLMError(f"Groq API request failed: {exc}") from exc

                elapsed_ms = int((time.perf_counter() - started) * 1000)

                if response.status_code in (401, 403):
                    raise LLMError("Groq rejected the API key (401/403). Check GROQ_API_KEY.")
                if response.status_code in (400, 404):
                    raise _ModelUnavailable(
                        f"model '{model}' unavailable: {_error_detail(response)}"
                    )
                if response.status_code in _RETRY_STATUSES:
                    if attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    raise LLMError(
                        f"Groq API is unavailable (HTTP {response.status_code}). "
                        "Please try again shortly."
                    )
                if response.status_code >= 400:
                    raise LLMError(
                        f"Groq API error (HTTP {response.status_code}): {_error_detail(response)}"
                    )

                body = response.json()
                choices = body.get("choices") or []
                content = ""
                if choices:
                    content = (choices[0].get("message") or {}).get("content") or ""
                if not content.strip():
                    raise LLMError("Groq returned an empty response.")

                usage = body.get("usage") or {}
                return LLMResult(
                    data=_parse_json_content(content),
                    model=body.get("model", model),
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    response_ms=elapsed_ms,
                )

        raise LLMError("Groq API request failed after retries.")


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
        return json.dumps(body)[:300]
    except Exception:  # pragma: no cover - defensive
        return response.text[:300]

"""The one place LLM calls go through, so switching provider later is a change here only."""

import json
import re
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app import telemetry, vault

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_RATE_LIMIT_RETRIES = 5
MAX_WAIT_SECONDS = 65
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(Exception):
    pass


def _wait_seconds(response: httpx.Response, attempt: int) -> float:
    """Groq sends retry-after, or x-ratelimit-reset-tokens like '3.4s' / '1m26.4s'."""
    if (retry_after := response.headers.get("retry-after")) is not None:
        try:
            return min(float(retry_after) + 0.5, MAX_WAIT_SECONDS)
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset-tokens", "")
    match = re.fullmatch(r"(?:(\d+)m)?([\d.]+)s", reset)
    if match:
        return min(int(match.group(1) or 0) * 60 + float(match.group(2)) + 0.5, MAX_WAIT_SECONDS)
    return min(2 ** attempt * 5, MAX_WAIT_SECONDS)


def _error_detail(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return f"HTTP {response.status_code}"


def complete(
    db: Session,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int = 2048,
    *,
    json_mode: bool = False,
    image_data_url: str | None = None,
    name: str = "llm",
    metadata: dict | None = None,
) -> str:
    api_key = vault.get_credential(db, "groq")["api_key"]
    content: Any = prompt
    if image_data_url:
        content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image_data_url}}]
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if model.startswith("openai/gpt-oss"):
        # Reasoning tokens count against the 8k tokens/minute limit; low is enough for these tasks.
        body["reasoning_effort"] = "low"

    with telemetry.observation(
        name, as_type="generation", model=model, input=prompt if not image_data_url else f"[image] {prompt}",
        model_parameters={"temperature": temperature, "max_tokens": max_tokens, "json": json_mode},
        metadata=metadata,
    ) as obs:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            telemetry.heartbeat()
            try:
                response = httpx.post(
                    GROQ_CHAT_URL, headers={"Authorization": f"Bearer {api_key}"}, json=body, timeout=120
                )
            except httpx.HTTPError as exc:
                if attempt < 2:
                    time.sleep(3)
                    continue
                raise LLMError(f"Could not reach Groq: {type(exc).__name__}")

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_RATE_LIMIT_RETRIES:
                    raise LLMError(f"Groq still unavailable after retries: {_error_detail(response)}")
                wait = _wait_seconds(response, attempt)
                if response.status_code == 429:
                    telemetry.notice(f"Waiting {wait:.0f}s for the Groq rate limit")
                    telemetry.count("rate_limit_wait_seconds", wait)
                else:
                    telemetry.notice(f"Groq returned HTTP {response.status_code}, retrying in {wait:.0f}s")
                time.sleep(wait)
                continue
            if response.status_code != 200:
                raise LLMError(f"Groq error: {_error_detail(response)}")
            break

        payload = response.json()
        text = payload["choices"][0]["message"].get("content") or ""
        text = _THINK_BLOCK.sub("", text).strip()
        usage = payload.get("usage") or {}
        telemetry.count("llm_calls")
        telemetry.count("prompt_tokens", usage.get("prompt_tokens", 0))
        telemetry.count("completion_tokens", usage.get("completion_tokens", 0))
        obs.update(
            output=text,
            usage_details={"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)},
        )
        if payload["choices"][0].get("finish_reason") == "length":
            raise LLMError(f"{name}: response cut off at {max_tokens} tokens")
        return text


def complete_json(db: Session, model: str, prompt: str, temperature: float, max_tokens: int = 4096, **kwargs: Any) -> dict:
    text = complete(db, model, prompt, temperature, max_tokens, json_mode=True, **kwargs)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise LLMError(f"{kwargs.get('name', 'llm')}: model did not return valid JSON")
    if not isinstance(value, dict):
        raise LLMError(f"{kwargs.get('name', 'llm')}: expected a JSON object")
    return value

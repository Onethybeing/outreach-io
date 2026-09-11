"""The one place LLM calls go through, so switching provider later is a change here only."""

import httpx
from sqlalchemy.orm import Session

from app import vault

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMError(Exception):
    pass


def complete(db: Session, model: str, prompt: str, temperature: float, max_tokens: int = 2048) -> str:
    api_key = vault.get_credential(db, "groq")["api_key"]
    try:
        response = httpx.post(
            GROQ_CHAT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_completion_tokens": max_tokens,
            },
            timeout=90,
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"Could not reach Groq: {type(exc).__name__}")
    if response.status_code != 200:
        try:
            detail = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = f"HTTP {response.status_code}"
        raise LLMError(f"Groq error: {detail}")
    return response.json()["choices"][0]["message"]["content"] or ""

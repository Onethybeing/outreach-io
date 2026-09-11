import time

import httpx
from sqlalchemy.orm import Session

from app import telemetry, vault

SEARCH_URL = "https://api.tavily.com/search"


class SearchError(Exception):
    pass


def search(
    db: Session,
    query: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
    content_chars: int = 400,
) -> list[dict]:
    """Basic-depth search (1 credit). Returns compact results: title, url, content."""
    api_key = vault.get_credential(db, "tavily")["api_key"]
    body: dict = {"query": query, "max_results": max_results, "search_depth": "basic"}
    if include_domains:
        body["include_domains"] = include_domains

    with telemetry.observation("tavily_search", as_type="tool", input=body) as obs:
        for attempt in range(3):
            telemetry.heartbeat()
            try:
                response = httpx.post(
                    SEARCH_URL, headers={"Authorization": f"Bearer {api_key}"}, json=body, timeout=60
                )
            except httpx.HTTPError as exc:
                if attempt < 2:
                    time.sleep(3)
                    continue
                raise SearchError(f"Could not reach Tavily: {type(exc).__name__}")
            if response.status_code >= 500 and attempt < 2:
                time.sleep(3)
                continue
            break

        if response.status_code == 401:
            raise SearchError("Tavily rejected the API key — check Settings → Vault")
        if response.status_code in (429, 432, 433):
            raise SearchError(f"Tavily usage limit reached (HTTP {response.status_code})")
        if response.status_code != 200:
            raise SearchError(f"Tavily error: HTTP {response.status_code}")

        telemetry.count("tavily_searches")
        results = [
            {
                "title": (r.get("title") or "").strip(),
                "url": r.get("url") or "",
                "content": (r.get("content") or "").strip()[:content_chars],
            }
            for r in response.json().get("results", [])
            if r.get("url")
        ]
        obs.update(output=results)
        return results

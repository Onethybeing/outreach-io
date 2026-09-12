import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from sqlalchemy.orm import Session

from app import httpclient, telemetry, vault

SEARCH_URL = "https://api.tavily.com/search"


class SearchError(Exception):
    pass


MAX_PARALLEL = 4  # independent searches; Tavily bills per call either way


def _run(
    api_key: str,
    query: str,
    max_results: int,
    include_domains: list[str] | None,
    content_chars: int,
) -> list[dict]:
    """One search (1 credit). No database access, so this is safe to call from a worker thread."""
    body: dict = {"query": query, "max_results": max_results, "search_depth": "basic"}
    if include_domains:
        body["include_domains"] = include_domains

    for attempt in range(3):
        try:
            response = httpclient.post(
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
        raise SearchError("Tavily rejected the API key. Check Settings > Vault")
    if response.status_code in (429, 432, 433):
        raise SearchError(f"Tavily usage limit reached (HTTP {response.status_code})")
    if response.status_code != 200:
        raise SearchError(f"Tavily error: HTTP {response.status_code}")

    return [
        {
            "title": (r.get("title") or "").strip(),
            "url": r.get("url") or "",
            "content": (r.get("content") or "").strip()[:content_chars],
        }
        for r in response.json().get("results", [])
        if r.get("url")
    ]


def search(
    db: Session,
    query: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
    content_chars: int = 400,
) -> list[dict]:
    """Basic-depth search (1 credit). Returns compact results: title, url, content."""
    api_key = vault.get_credential(db, "tavily")["api_key"]
    with telemetry.observation("tavily_search", as_type="tool", input={"query": query}) as obs:
        telemetry.heartbeat()
        results = _run(api_key, query, max_results, include_domains, content_chars)
        telemetry.count("tavily_searches")
        obs.update(output=results)
        return results


def search_many(
    db: Session,
    queries: list[str],
    max_results: int = 5,
    include_domains: list[str] | None = None,
    content_chars: int = 400,
) -> list[list[dict] | SearchError]:
    """Run independent searches at once, in the order given. A failure comes back in its own slot,
    so one bad query never costs the others.

    The credential is read once, on this thread: a Session must not be shared between threads.
    """
    if not queries:
        return []
    api_key = vault.get_credential(db, "tavily")["api_key"]
    if len(queries) == 1:
        try:
            with telemetry.observation("tavily_search", as_type="tool", input={"query": queries[0]}) as obs:
                telemetry.heartbeat()
                results = _run(api_key, queries[0], max_results, include_domains, content_chars)
                telemetry.count("tavily_searches")
                obs.update(output=results)
                return [results]
        except SearchError as exc:
            return [exc]

    out: list[list[dict] | SearchError] = [SearchError("not run")] * len(queries)
    # One span for the batch: OpenTelemetry's context doesn't follow a thread pool, so per-call
    # spans started in a worker would be orphaned from the run's trace.
    with telemetry.observation("tavily_search_batch", as_type="tool", input={"queries": queries}) as obs:
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(queries))) as pool:
            futures = {
                pool.submit(_run, api_key, q, max_results, include_domains, content_chars): i
                for i, q in enumerate(queries)
            }
            for future in as_completed(futures):
                index = futures[future]
                telemetry.heartbeat()  # on this thread, so a long batch can't look like a dead run
                try:
                    out[index] = future.result()
                    telemetry.count("tavily_searches")
                except SearchError as exc:
                    out[index] = exc
        obs.update(output={"found": [len(r) if isinstance(r, list) else 0 for r in out]})
    return out

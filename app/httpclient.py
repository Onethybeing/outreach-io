"""One shared HTTP client instead of a fresh connection per call.

Every provider call used to go through `httpx.get`/`httpx.post`, which opens a socket, does a TLS
handshake and throws it away. A run makes dozens of these, so the handshakes alone cost seconds.
A module-level client keeps connections alive per host; httpx.Client is safe to share across the
worker threads that run discovery and the bulk jobs.
"""

from functools import lru_cache

import httpx

# Enough for the parallel search fan-out plus the background job threads.
LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60)


@lru_cache
def client() -> httpx.Client:
    return httpx.Client(limits=LIMITS, follow_redirects=False)


def get(url: str, **kwargs) -> httpx.Response:
    return client().get(url, **kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    return client().post(url, **kwargs)


def close() -> None:
    """For tests and shutdown: the next call builds a fresh client."""
    if client.cache_info().currsize:
        client().close()
        client.cache_clear()

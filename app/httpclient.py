"""One shared HTTP client instead of a fresh connection per call.

Every provider call used to go through `httpx.get`/`httpx.post`, which opens a socket, does a TLS
handshake and throws it away. A run makes dozens of these, so the handshakes alone cost seconds.
A module-level client keeps connections alive per host; httpx.Client is safe to share across the
worker threads that run discovery and the bulk jobs.
"""

import threading

import httpx

# Enough for the parallel search fan-out plus the background job threads.
LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60)

_lock = threading.Lock()
_client: httpx.Client | None = None


def client() -> httpx.Client:
    """One client for the process. Built under a lock: two threads racing on a cold start would
    otherwise each build one, and the loser's connection pool would never be closed."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = httpx.Client(limits=LIMITS, follow_redirects=False)
    return _client


def get(url: str, **kwargs) -> httpx.Response:
    return client().get(url, **kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    return client().post(url, **kwargs)


def close() -> None:
    """For shutdown and tests: the next call builds a fresh client."""
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None

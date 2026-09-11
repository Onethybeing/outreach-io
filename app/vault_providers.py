"""What each provider's credential looks like, and a free call that proves it works."""

import base64
from collections.abc import Callable
from dataclasses import dataclass

import httpx

TIMEOUT = 20


@dataclass(frozen=True)
class ProviderSpec:
    fields: tuple[str, ...]
    secret_field: str  # the field whose last 4 characters are shown in the UI
    test: Callable[[dict], tuple[bool, str]]


def _bearer_get(url: str, token: str) -> httpx.Response:
    return httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)


def _result(response: httpx.Response, ok_message: str) -> tuple[bool, str]:
    if response.status_code == 200:
        return True, ok_message
    return False, f"HTTP {response.status_code}"


def _test_groq(c: dict) -> tuple[bool, str]:
    return _result(_bearer_get("https://api.groq.com/openai/v1/models", c["api_key"]), "Key valid")


def _test_tavily(c: dict) -> tuple[bool, str]:
    # /usage costs no search credits.
    return _result(_bearer_get("https://api.tavily.com/usage", c["api_key"]), "Key valid")


def _test_brightdata(c: dict) -> tuple[bool, str]:
    r = _bearer_get("https://api.brightdata.com/zone/get_active_zones", c["api_key"])
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    return True, f"Key valid, {len(r.json())} active zone(s)"


def _test_apollo(c: dict) -> tuple[bool, str]:
    r = httpx.get(
        "https://api.apollo.io/v1/auth/health",
        headers={"X-Api-Key": c["api_key"], "Cache-Control": "no-cache"},
        timeout=TIMEOUT,
    )
    # Apollo answers 200 for a wrong key too; only is_logged_in tells them apart.
    if r.status_code == 200 and r.json().get("is_logged_in") is True:
        return True, "Key valid"
    return False, "Key rejected by Apollo"


def _test_langfuse(c: dict) -> tuple[bool, str]:
    basic = base64.b64encode(f"{c['public_key']}:{c['secret_key']}".encode()).decode()
    r = httpx.get(
        f"{c['host'].rstrip('/')}/api/public/projects",
        headers={"Authorization": f"Basic {basic}"},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    names = [p.get("name") for p in r.json().get("data", [])]
    return True, f"Key valid, project: {', '.join(names)}"


def _test_gmail(c: dict) -> tuple[bool, str]:
    token = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "refresh_token": c["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT,
    )
    if token.status_code != 200:
        return False, "Refresh token rejected — rerun scripts/gmail_auth.py"
    profile = _bearer_get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile", token.json()["access_token"]
    )
    if profile.status_code != 200:
        return False, f"Gmail profile HTTP {profile.status_code}"
    account = profile.json()["emailAddress"]
    if account.lower() != c["sender_address"].lower():
        return False, f"Token belongs to {account}, not {c['sender_address']}"
    return True, f"Signed in as {account}"


PROVIDERS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(("api_key",), "api_key", _test_groq),
    "tavily": ProviderSpec(("api_key",), "api_key", _test_tavily),
    "apollo": ProviderSpec(("api_key",), "api_key", _test_apollo),
    "brightdata": ProviderSpec(("api_key",), "api_key", _test_brightdata),
    "langfuse": ProviderSpec(("public_key", "secret_key", "host"), "secret_key", _test_langfuse),
    "gmail": ProviderSpec(
        ("client_id", "client_secret", "refresh_token", "sender_address"),
        "refresh_token",
        _test_gmail,
    ),
}

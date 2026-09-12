"""Minimal Gmail read client (REST). Checked live: token refresh, message search, full message fetch."""

import base64
import html
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr

import httpx
from sqlalchemy.orm import Session

from app import telemetry, vault

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_BODY_CHARS = 4000

_token: tuple[tuple[str, str], str, float] | None = None  # (client_id, refresh_token), token, expires (monotonic)


class GmailError(Exception):
    """One request failed (rate limit, server error). Other contacts can still be checked."""


class GmailAuthError(GmailError):
    """Sign-in expired or revoked: every request will fail, so stop."""


class GmailNotFound(GmailError):
    """The thread or message no longer exists (deleted), so skip it."""


@dataclass
class GmailMessage:
    id: str
    thread_id: str
    from_address: str
    subject: str
    received_at: datetime
    snippet: str
    body_text: str
    auto_submitted: bool


def _access_token(db: Session, force_refresh: bool = False) -> str:
    global _token
    creds = vault.get_credential(db, "gmail")
    key = (creds["client_id"], creds["refresh_token"])
    if not force_refresh and _token and _token[0] == key and _token[2] > time.monotonic() + 60:
        return _token[1]
    response = httpx.post(TOKEN_URL, data={
        "client_id": creds["client_id"], "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"], "grant_type": "refresh_token",
    }, timeout=30)
    if response.status_code != 200:
        raise GmailAuthError("Gmail sign-in expired or was revoked. Run scripts/gmail_auth.py again and update the vault")
    body = response.json()
    _token = (key, body["access_token"], time.monotonic() + int(body.get("expires_in", 3600)))
    return _token[1]


def _get(db: Session, path: str, params: dict) -> dict:
    for attempt in range(2):
        telemetry.heartbeat()
        try:
            response = httpx.get(f"{API}{path}", headers={"Authorization": f"Bearer {_access_token(db, force_refresh=attempt > 0)}"},
                                 params=params, timeout=30)
        except httpx.HTTPError as exc:
            raise GmailError(f"Could not reach Gmail: {type(exc).__name__}")
        if response.status_code == 401 and attempt == 0:
            continue  # token expired early; refresh once
        if response.status_code == 404:
            raise GmailNotFound(f"Gmail item not found: {path}")
        if response.status_code != 200:
            raise GmailError(f"Gmail error (HTTP {response.status_code})")
        return response.json()
    raise GmailAuthError("Gmail kept rejecting the access token. Sign in again with scripts/gmail_auth.py")


def search(db: Session, query: str, max_results: int = 20) -> list[str]:
    data = _get(db, "/messages", {"q": query, "maxResults": max_results})
    return [m["id"] for m in data.get("messages", [])]


def thread_message_ids(db: Session, thread_id: str) -> list[str]:
    data = _get(db, f"/threads/{thread_id}", {"format": "minimal"})
    return [m["id"] for m in data.get("messages", [])]


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _text_from_payload(payload: dict) -> str:
    plain, rich = [], []

    def walk(part: dict) -> None:
        mime, data = part.get("mimeType", ""), (part.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_decode(data))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    if plain:
        return "\n".join(plain)
    text = re.sub(r"<(script|style).*?</\1>|<[^>]+>", " ", "\n".join(rich), flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_message(raw: dict) -> GmailMessage:
    headers = {h["name"].lower(): h["value"] for h in (raw.get("payload") or {}).get("headers", [])}
    auto = headers.get("auto-submitted", "no").lower()
    return GmailMessage(
        id=raw["id"],
        thread_id=raw.get("threadId", ""),
        from_address=parseaddr(headers.get("from", ""))[1].lower(),
        subject=headers.get("subject", ""),
        received_at=datetime.fromtimestamp(int(raw.get("internalDate", "0")) / 1000, tz=timezone.utc),
        snippet=html.unescape(raw.get("snippet", "")),
        body_text=_text_from_payload(raw.get("payload") or {})[:MAX_BODY_CHARS],
        auto_submitted=auto not in ("", "no"),
    )


def get_message(db: Session, message_id: str) -> GmailMessage:
    return parse_message(_get(db, f"/messages/{message_id}", {"format": "full"}))


SCOPE_HINTS = ("insufficient", "scope", "accessnotconfigured", "forbidden for this user")


def _send_failure(response: httpx.Response) -> GmailError:
    """403 covers both a missing scope (stop) and a rate/quota limit (retry later), so tell them apart."""
    text = response.text[:500]
    if response.status_code == 403 and any(hint in text.lower() for hint in SCOPE_HINTS):
        return GmailAuthError("Gmail refused the send: the token is missing the gmail.send scope")
    if response.status_code in (403, 429):
        return GmailError(f"Gmail is rate-limiting or over quota (HTTP {response.status_code}). Try again later")
    return GmailError(f"Gmail refused the send (HTTP {response.status_code}): {text[:200]}")


def send_message(db: Session, raw_message: bytes) -> tuple[str, str]:
    """Send a built MIME message. Returns (gmail message id, thread id)."""
    payload = {"raw": base64.urlsafe_b64encode(raw_message).decode()}
    last = 2 - 1
    for attempt in range(2):
        telemetry.heartbeat()
        try:
            response = httpx.post(
                f"{API}/messages/send",
                headers={"Authorization": f"Bearer {_access_token(db, force_refresh=attempt > 0)}"},
                json=payload, timeout=60,
            )
        except httpx.HTTPError as exc:
            # The message may still have been accepted, so the caller must check before retrying.
            raise GmailError(f"Could not reach Gmail: {type(exc).__name__}")
        if response.status_code == 401:
            if attempt < last:
                continue  # token expired early; refresh once
            raise GmailAuthError("Gmail kept rejecting the access token. Sign in again with scripts/gmail_auth.py")
        if response.status_code not in (200, 201):
            raise _send_failure(response)
        body = response.json()
        return body["id"], body.get("threadId", "")
    raise GmailAuthError("Gmail kept rejecting the access token. Sign in again with scripts/gmail_auth.py")


def find_by_rfc_message_id(db: Session, rfc_message_id: str) -> tuple[str, str] | None:
    """Find a message we built by its MIME Message-ID. Used to check whether a send that appeared
    to fail actually went out. Returns (gmail message id, thread id)."""
    ids = search(db, f"rfc822msgid:{rfc_message_id}", max_results=1)
    if not ids:
        return None
    data = _get(db, f"/messages/{ids[0]}", {"format": "minimal"})
    return data["id"], data.get("threadId", "")

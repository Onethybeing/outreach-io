"""Session cookies, current-user lookup, permission checks, and Google sign-in (PLAN.md §9)."""

import uuid
from urllib.parse import urlencode

import httpx
from fastapi import Depends, HTTPException, Request, status
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import User
from app.rbac import has_permission

SESSION_COOKIE = "outreach_session"
SESSION_MAX_AGE = 7 * 24 * 3600
STATE_COOKIE = "outreach_oauth_state"
STATE_MAX_AGE = 10 * 60

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _serializer(salt: str) -> URLSafeTimedSerializer:
    secret = get_settings().session_secret
    if not secret:
        raise RuntimeError("SESSION_SECRET is not set")
    return URLSafeTimedSerializer(secret, salt=salt)


def cookie_secure() -> bool:
    return get_settings().public_base_url.startswith("https://")


def sign_session(user_id: uuid.UUID) -> str:
    return _serializer("session").dumps({"uid": str(user_id)})


def sign_oauth_state(state: str, nonce: str) -> str:
    return _serializer("oauth-state").dumps({"state": state, "nonce": nonce})


def read_oauth_state(value: str) -> dict:
    return _serializer("oauth-state").loads(value, max_age=STATE_MAX_AGE)


def _user_from_cookie(db: Session, raw: str | None) -> User:
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    try:
        data = _serializer("session").loads(raw, max_age=SESSION_MAX_AGE)
        user_id = uuid.UUID(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalid or expired")

    # Loaded fresh every request, so role changes and deactivation apply immediately.
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not active")
    return user


def _check(user: User, permission: str) -> None:
    if not has_permission(user.role, permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    return _user_from_cookie(db, request.cookies.get(SESSION_COOKIE))


def require(permission: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        _check(user, permission)
        return user

    return dependency


def require_for_stream(permission: str):
    """Auth for long-lived responses (SSE). Uses its own session, closed before streaming starts —
    `get_db` would hold a pooled connection until the response ends. Returns only the user id."""

    def dependency(request: Request) -> uuid.UUID:
        with SessionLocal() as db:
            user = _user_from_cookie(db, request.cookies.get(SESSION_COOKIE))
            _check(user, permission)
            return user.id

    return dependency


def login_redirect_uri() -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/auth/callback"


def google_authorize_url(state: str, nonce: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_login_client_id,
        "redirect_uri": login_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_claims(code: str, expected_nonce: str) -> dict:
    """Swap the auth code for an ID token and return its verified claims."""
    settings = get_settings()
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_login_client_id,
            "client_secret": settings.google_login_client_secret,
            "redirect_uri": login_redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed")

    # Checks signature, audience (our client id), issuer and expiry.
    claims = id_token.verify_oauth2_token(
        response.json()["id_token"], GoogleRequest(), settings.google_login_client_id
    )
    if claims.get("nonce") != expected_nonce:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed (nonce)")
    if not claims.get("email_verified"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Google email is not verified")
    return claims

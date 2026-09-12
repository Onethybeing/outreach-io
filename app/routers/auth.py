import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    STATE_COOKIE,
    STATE_MAX_AGE,
    cookie_secure,
    exchange_code_for_claims,
    get_current_user,
    google_authorize_url,
    read_oauth_state,
    sign_oauth_state,
    sign_session,
)
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.rbac import PERMISSIONS
from app.schemas import MeOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
def login() -> RedirectResponse:
    if not get_settings().google_login_client_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in is not configured")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    response = RedirectResponse(google_authorize_url(state, nonce), status.HTTP_302_FOUND)
    response.set_cookie(
        STATE_COOKIE, sign_oauth_state(state, nonce), max_age=STATE_MAX_AGE,
        httponly=True, secure=cookie_secure(), samesite="lax",
    )
    return response


@router.get("/callback")
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Google sign-in cancelled: {error}")
    try:
        saved = read_oauth_state(request.cookies.get(STATE_COOKIE, ""))
    except BadSignature:  # also covers expiry
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sign-in expired. Start again at /auth/login")
    if not code or not state or not secrets.compare_digest(state, saved["state"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sign-in state mismatch. Start again")

    claims = exchange_code_for_claims(code, saved["nonce"])
    email = claims["email"].lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None or not user.is_active:
        audit.record(db, None, "auth.login_denied", "email", email)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{email} does not have access. Ask an admin")

    user.last_login_at = datetime.now(timezone.utc)
    if not user.name and claims.get("name"):
        user.name = claims["name"]
    audit.record(db, user, "auth.login", "user", user.id)
    db.commit()

    # Server-side setting, never taken from the request, so this can't become an open redirect.
    response = RedirectResponse(get_settings().post_login_redirect, status.HTTP_302_FOUND)
    response.set_cookie(
        SESSION_COOKIE, sign_session(user.id), max_age=SESSION_MAX_AGE,
        httponly=True, secure=cookie_secure(), samesite="lax",
    )
    response.delete_cookie(STATE_COOKIE)
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)) -> MeOut:
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        permissions=sorted(PERMISSIONS[user.role]),
    )

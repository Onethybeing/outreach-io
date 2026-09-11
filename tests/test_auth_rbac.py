from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select

from app.auth import SESSION_COOKIE, STATE_COOKIE, sign_oauth_state
from app.models import AuditLog, User, UserRole
from app.rbac import PERMISSIONS
from app.routers import auth as auth_router


def test_no_cookie_is_401(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/resumes").status_code == 401


def test_tampered_cookie_is_401(client, make_user):
    user = make_user(UserRole.admin)
    forged = URLSafeTimedSerializer("not-the-real-secret", salt="session").dumps({"uid": str(user.id)})
    client.cookies.set(SESSION_COOKIE, forged)
    assert client.get("/auth/me").status_code == 401


def test_deactivated_user_loses_access_on_next_request(db, make_user, login):
    user = make_user(UserRole.operator)
    client = login(user)
    assert client.get("/auth/me").status_code == 200
    user.is_active = False
    db.commit()
    assert client.get("/auth/me").status_code == 401


def test_me_lists_role_permissions(make_user, login):
    body = login(make_user(UserRole.operator)).get("/auth/me").json()
    assert body["role"] == "operator"
    assert set(body["permissions"]) == PERMISSIONS[UserRole.operator]


def test_viewer_can_read_but_not_act(make_user, login):
    client = login(make_user(UserRole.viewer))
    assert client.get("/resumes").status_code == 200
    upload = client.post("/resumes", files={"file": ("cv.pdf", b"%PDF-1.4", "application/pdf")})
    assert upload.status_code == 403
    assert client.get("/prompts").status_code == 403


def test_operator_cannot_touch_admin_areas(make_user, login):
    client = login(make_user(UserRole.operator))
    assert client.get("/prompts").status_code == 200
    assert client.get("/vault").status_code == 403
    assert client.get("/users").status_code == 403
    assert client.get("/audit-log").status_code == 403
    assert client.post("/prompts/classify_reply/reset").status_code == 403


def test_admin_reaches_admin_areas(make_user, login):
    client = login(make_user(UserRole.admin))
    for path in ("/vault", "/users", "/audit-log", "/prompts"):
        assert client.get(path).status_code == 200, path


def test_role_change_applies_immediately(db, make_user, login):
    user = make_user(UserRole.admin)
    client = login(user)
    assert client.get("/vault").status_code == 200
    user.role = UserRole.viewer
    db.commit()
    assert client.get("/vault").status_code == 403


def test_last_active_admin_cannot_be_removed(db, make_user, login):
    for other in db.scalars(select(User).where(User.role == UserRole.admin)):
        other.is_active = False
    db.commit()
    admin = make_user(UserRole.admin)
    client = login(admin)

    assert client.patch(f"/users/{admin.id}", json={"role": "operator"}).status_code == 400
    assert client.patch(f"/users/{admin.id}", json={"is_active": False}).status_code == 400

    second = make_user(UserRole.admin)
    assert client.patch(f"/users/{second.id}", json={"role": "operator"}).status_code == 200


def test_create_user_normalizes_and_rejects_duplicates(make_user, login):
    client = login(make_user(UserRole.admin))
    created = client.post("/users", json={"email": "  New.Person@Test.Example ", "role": "operator"})
    assert created.status_code == 201
    assert created.json()["email"] == "new.person@test.example"
    assert client.post("/users", json={"email": "new.person@test.example"}).status_code == 409
    assert client.post("/users", json={"email": "not-an-email"}).status_code == 422


def _callback(client, monkeypatch, email: str):
    monkeypatch.setattr(
        auth_router, "exchange_code_for_claims",
        lambda code, nonce: {"email": email, "email_verified": True, "name": "Test Person", "nonce": nonce},
    )
    client.cookies.set(STATE_COOKIE, sign_oauth_state("the-state", "the-nonce"))
    return client.get("/auth/callback?code=abc&state=the-state", follow_redirects=False)


def test_google_callback_signs_in_known_user(db, client, make_user, monkeypatch):
    user = make_user(UserRole.operator, email="known@test.example")
    response = _callback(client, monkeypatch, "Known@Test.Example")
    assert response.status_code == 302
    assert SESSION_COOKIE in response.cookies
    db.refresh(user)
    assert user.last_login_at is not None
    assert client.get("/auth/me").json()["email"] == "known@test.example"


def test_google_callback_denies_unknown_email_and_audits(db, client, monkeypatch):
    response = _callback(client, monkeypatch, "stranger@test.example")
    assert response.status_code == 403
    assert SESSION_COOKIE not in response.cookies
    denied = db.scalar(
        select(AuditLog).where(AuditLog.action == "auth.login_denied", AuditLog.target_id == "stranger@test.example")
    )
    assert denied is not None


def test_google_callback_rejects_state_mismatch(client, monkeypatch, make_user):
    make_user(UserRole.admin, email="known2@test.example")
    monkeypatch.setattr(auth_router, "exchange_code_for_claims", lambda code, nonce: {"email": "known2@test.example"})
    client.cookies.set(STATE_COOKIE, sign_oauth_state("the-state", "the-nonce"))
    assert client.get("/auth/callback?code=abc&state=attacker-state", follow_redirects=False).status_code == 400

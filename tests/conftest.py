"""Tests run against the real Postgres in DATABASE_URL, inside a transaction that is always rolled
back — app code can commit freely (commits become savepoints) and nothing persists."""

import uuid
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import httpclient, vault
from app.auth import SESSION_COOKIE, sign_session
from app.config import get_settings
from app.db import engine, get_db
from app.main import app
from app.storage import get_storage
from app.models import User, UserRole
from app.vault_providers import PROVIDERS, ProviderSpec


@pytest.fixture(autouse=True)
def test_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "vault_master_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "session_secret", "test-session-secret-" + "x" * 32)
    monkeypatch.setattr(settings, "public_base_url", "http://testserver")
    # Every test gets its own local file storage, so no test touches storage/ or a bucket.
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path))
    get_storage.cache_clear()
    vault.clear_cache()
    yield
    get_storage.cache_clear()


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any provider call a test forgot to fake fails loudly instead of reaching the internet.

    Tests patch `httpclient.get`/`post`, so a real request only happens when a patch is missing or
    points at the wrong place. That used to look like a puzzling assertion failure (or a real
    charge); now it names itself.
    """

    def blocked(url: str, **_: object) -> None:
        raise AssertionError(f"This test tried to call {url} for real. Patch httpclient.get/post.")

    monkeypatch.setattr(httpclient, "get", blocked)
    monkeypatch.setattr(httpclient, "post", blocked)


@pytest.fixture
def db() -> Iterator[Session]:
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    # No `with`: skips the startup bootstrap, tests set up exactly the state they need.
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(db: Session):
    def _make(role: UserRole, email: str | None = None, active: bool = True) -> User:
        user = User(
            email=email or f"{role.value}-{uuid.uuid4().hex[:12]}@test.example", role=role, is_active=active
        )
        db.add(user)
        db.commit()
        return user

    return _make


@pytest.fixture
def login(client: TestClient):
    def _login(user: User) -> TestClient:
        client.cookies.set(SESSION_COOKIE, sign_session(user.id))
        return client

    return _login


@pytest.fixture
def fake_provider_test(monkeypatch: pytest.MonkeyPatch):
    """Replace a provider's live key check. The fake accepts keys starting with 'good'."""

    def _install(provider: str = "groq") -> list[dict]:
        calls: list[dict] = []
        spec = PROVIDERS[provider]

        def fake(values: dict) -> tuple[bool, str]:
            calls.append(values)
            ok = values[spec.secret_field].startswith("good")
            return (True, "Key valid") if ok else (False, "HTTP 401")

        monkeypatch.setitem(PROVIDERS, provider, ProviderSpec(spec.fields, spec.secret_field, fake))
        return calls

    return _install

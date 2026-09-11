from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select

from app import vault
from app.config import Settings, get_settings
from app.models import ApiCredential, AuditLog, CredentialStatus, UserRole


@pytest.fixture(autouse=True)
def empty_vault(db):
    # Rows seeded in the real DB were encrypted with the real key; start each test clean.
    db.execute(delete(ApiCredential))
    db.commit()


def _versions(db, provider="groq"):
    return list(db.scalars(select(ApiCredential).where(ApiCredential.provider == provider).order_by(ApiCredential.version)))


def test_failed_key_test_changes_nothing(db, make_user, login, fake_provider_test):
    fake_provider_test()
    client = login(make_user(UserRole.admin))
    response = client.put("/vault/groq", json={"values": {"api_key": "bad-key-1234"}})
    assert response.status_code == 400
    assert "nothing changed" in response.json()["detail"]
    assert _versions(db) == []
    assert db.scalar(select(AuditLog).where(AuditLog.action == "vault.rotate_rejected")) is not None


def test_rotate_retires_old_version_and_never_returns_secret(db, make_user, login, fake_provider_test):
    fake_provider_test()
    client = login(make_user(UserRole.admin))
    assert client.put("/vault/groq", json={"values": {"api_key": "good-first-AAAA"}}).status_code == 200
    assert client.put("/vault/groq", json={"values": {"api_key": "good-second-BBBB"}}).status_code == 200

    rows = _versions(db)
    assert [(r.version, r.status) for r in rows] == [(1, CredentialStatus.retired), (2, CredentialStatus.active)]
    assert all("good-" not in r.encrypted_value for r in rows)  # stored encrypted

    listing = client.get("/vault").text + client.get("/vault/groq/versions").text
    assert "good-first" not in listing and "good-second" not in listing
    groq = next(p for p in client.get("/vault").json() if p["provider"] == "groq")
    assert groq["last4"] == "BBBB" and groq["version"] == 2 and groq["last_test_ok"] is True


def test_get_credential_sees_rotation_immediately(db, make_user, fake_provider_test):
    fake_provider_test()
    admin = make_user(UserRole.admin)
    vault.save_credential(db, "groq", {"api_key": "good-one"}, admin)
    assert vault.get_credential(db, "groq")["api_key"] == "good-one"  # now cached
    vault.save_credential(db, "groq", {"api_key": "good-two"}, admin)
    assert vault.get_credential(db, "groq")["api_key"] == "good-two"


def test_rollback_restores_previous_key(db, make_user, login, fake_provider_test):
    fake_provider_test()
    client = login(make_user(UserRole.admin))
    client.put("/vault/groq", json={"values": {"api_key": "good-old"}})
    client.put("/vault/groq", json={"values": {"api_key": "good-new"}})
    assert client.post("/vault/groq/rollback/1").status_code == 200
    assert vault.get_credential(db, "groq")["api_key"] == "good-old"
    assert {r.version: r.status for r in _versions(db)} == {1: CredentialStatus.active, 2: CredentialStatus.retired}
    assert client.post("/vault/groq/rollback/1").status_code == 400  # already active


def test_rollback_refuses_old_key_that_no_longer_works(db, make_user, fake_provider_test):
    calls = fake_provider_test()
    admin = make_user(UserRole.admin)
    vault.save_credential(db, "groq", {"api_key": "good-old"}, admin)
    vault.save_credential(db, "groq", {"api_key": "good-new"}, admin)
    # Simulate the provider having revoked the old key.
    old = _versions(db)[0]
    old.encrypted_value = vault._encrypt({"api_key": "revoked-old"})
    db.commit()
    with pytest.raises(vault.VaultError, match="nothing changed"):
        vault.rollback(db, "groq", 1, admin)
    assert vault.get_credential(db, "groq")["api_key"] == "good-new"
    assert calls[-1] == {"api_key": "revoked-old"}


def test_retired_versions_older_than_7_days_are_purged(db, make_user, fake_provider_test):
    fake_provider_test()
    admin = make_user(UserRole.admin)
    vault.save_credential(db, "groq", {"api_key": "good-1"}, admin)
    vault.save_credential(db, "groq", {"api_key": "good-2"}, admin)
    _versions(db)[0].retired_at = datetime.now(timezone.utc) - timedelta(days=8)
    db.commit()
    vault.save_credential(db, "groq", {"api_key": "good-3"}, admin)
    assert [r.version for r in _versions(db)] == [2, 3]


@pytest.mark.parametrize(
    "provider, values, message",
    [
        ("nope", {"api_key": "good"}, "Unknown provider"),
        ("groq", {"api_key": "   "}, "Missing field"),
        ("groq", {"api_key": "good", "extra": "x"}, "Unknown field"),
        ("langfuse", {"public_key": "pk", "secret_key": "good"}, "Missing field(s) for langfuse: host"),
    ],
)
def test_input_validation(db, make_user, provider, values, message):
    with pytest.raises(vault.VaultError, match=message.replace("(", r"\(").replace(")", r"\)")):
        vault.save_credential(db, provider, values, make_user(UserRole.admin))


def test_missing_provider_gives_clear_error(db):
    with pytest.raises(vault.VaultError, match="not configured"):
        vault.get_credential(db, "apollo")


def test_changed_master_key_is_reported_not_silently_wrong(db, make_user, fake_provider_test, monkeypatch):
    fake_provider_test()
    vault.save_credential(db, "groq", {"api_key": "good"}, make_user(UserRole.admin))
    vault.clear_cache()
    monkeypatch.setattr(get_settings(), "vault_master_key", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="VAULT_MASTER_KEY changed"):
        vault.get_credential(db, "groq")


def test_seed_imports_env_once_and_skips_incomplete_providers(db):
    settings = Settings(
        _env_file=None, groq_api_key="gsk-from-env", langfuse_public_key="pk", langfuse_secret_key="",
    )
    assert vault.seed_from_env(db, settings) == ["groq"]  # langfuse incomplete → skipped
    assert vault.get_credential(db, "groq")["api_key"] == "gsk-from-env"
    assert vault.seed_from_env(db, settings) == []  # never re-imports over an existing entry

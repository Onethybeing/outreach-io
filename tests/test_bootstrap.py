import pytest
from sqlalchemy import select

from app import bootstrap
from app.config import Settings
from app.models import User, UserRole


@pytest.fixture
def no_admins(db):
    for user in db.scalars(select(User).where(User.role == UserRole.admin)):
        user.is_active = False
    db.commit()


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_creates_initial_admin_when_none_exists(db, no_admins):
    bootstrap.ensure_initial_admin(db, _settings(initial_admin_email=" First.Admin@Test.Example "))
    user = db.scalar(select(User).where(User.email == "first.admin@test.example"))
    assert user.role == UserRole.admin and user.is_active


def test_does_not_override_existing_admin_setup(db, make_user):
    make_user(UserRole.admin)
    demoted = make_user(UserRole.viewer, email="was.admin@test.example")
    bootstrap.ensure_initial_admin(db, _settings(initial_admin_email="was.admin@test.example"))
    db.refresh(demoted)
    assert demoted.role == UserRole.viewer


def test_recovers_lockout_by_promoting_existing_user(db, no_admins, make_user):
    user = make_user(UserRole.viewer, email="recover@test.example", active=False)
    bootstrap.ensure_initial_admin(db, _settings(initial_admin_email="recover@test.example"))
    db.refresh(user)
    assert user.role == UserRole.admin and user.is_active


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"vault_master_key": "not-a-fernet-key", "session_secret": "s" * 40}, "VAULT_MASTER_KEY"),
        ({"vault_master_key": "", "session_secret": "s" * 40}, "VAULT_MASTER_KEY"),
        ({"session_secret": "short"}, "SESSION_SECRET"),
    ],
)
def test_bad_bootstrap_secrets_fail_fast(overrides, message):
    from cryptography.fernet import Fernet

    settings = _settings(**{"vault_master_key": Fernet.generate_key().decode(), **overrides})
    with pytest.raises(RuntimeError, match=message):
        bootstrap.check_secrets(settings)

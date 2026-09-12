"""Runs once at startup: check bootstrap secrets, create the first admin, seed prompts and vault."""

import logging

from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, prompts, vault
from app.contacts import sending, verification
from app.discovery import graph as discovery
from app.config import Settings
from app.models import User, UserRole

logger = logging.getLogger(__name__)


def check_secrets(settings: Settings) -> None:
    try:
        Fernet(settings.vault_master_key.encode())
    except ValueError:
        raise RuntimeError(
            "VAULT_MASTER_KEY is missing or invalid. Generate one with: python -c "
            "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    if len(settings.session_secret) < 32:
        raise RuntimeError("SESSION_SECRET must be at least 32 characters")


def ensure_initial_admin(db: Session, settings: Settings) -> None:
    """Only acts when there is no active admin, so it can't override a deliberate demotion."""
    email = settings.initial_admin_email.strip().lower()
    has_admin = db.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.admin, User.is_active)
    )
    if has_admin or not email:
        if not has_admin:
            logger.warning("No active admin and INITIAL_ADMIN_EMAIL is empty, so nobody can manage users")
        return

    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        user = User(email=email, role=UserRole.admin)
        db.add(user)
        db.flush()
    else:
        user.role, user.is_active = UserRole.admin, True
    audit.record(db, None, "users.bootstrap_admin", "user", user.id, {"email": email})
    db.commit()
    logger.info("Initial admin set: %s", email)


def run(db: Session, settings: Settings) -> None:
    check_secrets(settings)
    ensure_initial_admin(db, settings)
    if seeded := prompts.seed_defaults(db):
        logger.info("Seeded default prompts: %s", ", ".join(seeded))
    if upgraded := prompts.upgrade_system_defaults(db):
        logger.info("Upgraded untouched default prompts: %s", ", ".join(upgraded))
    if seeded := vault.seed_from_env(db, settings):
        logger.info("Imported keys from .env into the vault: %s", ", ".join(seeded))
    if interrupted := discovery.fail_interrupted_runs(db):
        logger.warning("Marked %d interrupted discovery run(s) as failed", interrupted)
    if interrupted := verification.fail_interrupted_jobs(db):
        logger.warning("Marked %d interrupted contact verification/lookup job(s) as failed", interrupted)
    if interrupted := sending.fail_interrupted_sends(db):
        logger.warning("Marked %d interrupted send(s) as failed", interrupted)

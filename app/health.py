"""The Settings health panel: one place to see whether the parts this app depends on are working.

Each check answers "would this fail right now, and what would I do about it". Provider checks reuse
the same free calls the vault uses to test a key, so nothing here spends credits.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import vault
from app.config import get_settings
from app.contacts import sending, service
from app.models import ApiCredential, CredentialStatus, Prompt, Resume, Run, RunStatus
from app.prompt_defaults import NODES
from app.storage import get_storage
from app.vault_providers import PROVIDERS

logger = logging.getLogger(__name__)

# Gmail's refresh token expires after 7 days while the Google app is unpublished (in Testing).
GMAIL_TOKEN_DAYS = 7
GMAIL_WARN_AFTER = timedelta(days=5)


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail | off
    detail: str
    hint: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _database(db: Session) -> Check:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.exception("Health: database unreachable")
        return Check("Database", "fail", f"Unreachable ({type(exc).__name__})", "Check the Neon project is awake and DATABASE_URL is right")
    return Check("Database", "ok", "Reachable")


def _storage() -> Check:
    settings = get_settings()
    where = f"bucket {settings.gcs_bucket}" if settings.storage_backend == "gcs" else f"local {settings.local_storage_dir}"
    try:
        key = "health/.probe"
        storage = get_storage()
        storage.save(key, b"ok", "text/plain")
        storage.delete(key)
    except Exception as exc:  # noqa: BLE001 — any backend error is the same answer to the user
        logger.exception("Health: storage write failed")
        return Check("File storage", "fail", f"{where}: cannot write ({type(exc).__name__})", "CV uploads and sent-email copies will fail")
    return Check("File storage", "ok", f"{where}: read and write work")


def _provider(db: Session, name: str) -> Check:
    label = name.replace("_", " ").title()
    try:
        values = vault.get_credential(db, name)
    except vault.VaultError:
        return Check(label, "off", "No key saved", f"Add it in Settings > Vault")
    try:
        ok, message = PROVIDERS[name].test(values)
    except Exception as exc:  # noqa: BLE001 — a provider being unreachable is a normal answer here
        return Check(label, "fail", f"Could not reach it ({type(exc).__name__})", "Try again, or check the provider's status page")
    return Check(label, "ok" if ok else "fail", message, None if ok else "Rotate the key in Settings > Vault")


def _gmail_age(db: Session) -> Check | None:
    """How long the Gmail credential has been in place, since the token expires while unpublished."""
    saved = db.scalar(
        select(func.max(ApiCredential.created_at)).where(
            ApiCredential.provider == "gmail", ApiCredential.status == CredentialStatus.active
        )
    )
    if saved is None:
        return None
    age = _now() - saved
    days = age.days
    if age < GMAIL_WARN_AFTER:
        return Check("Gmail token age", "ok", f"Saved {days} day(s) ago")
    status = "fail" if age >= timedelta(days=GMAIL_TOKEN_DAYS) else "warn"
    return Check(
        "Gmail token age", status, f"Saved {days} day(s) ago",
        f"Google expires it after {GMAIL_TOKEN_DAYS} days while the app is unpublished. "
        "Run scripts/gmail_auth.py and rotate the gmail credential",
    )


def _prompts(db: Session) -> Check:
    active = {p.node_name for p in db.scalars(select(Prompt).where(Prompt.is_active))}
    missing = sorted(set(NODES) - active)
    if missing:
        return Check("Node prompts", "fail", f"No active version for: {', '.join(missing)}", "Reset those nodes in Settings > Prompts")
    return Check("Node prompts", "ok", f"All {len(NODES)} nodes have an active version")


def _sending(db: Session) -> Check:
    mode = service.app_mode(db)
    if mode == "prod":
        return Check("Sending mode", "warn", "Production: emails go to the contacts themselves")
    target = sending.dev_redirect_address(db)
    if "@" not in target or target.endswith("@localhost"):
        return Check("Sending mode", "fail", "Dev, but no redirect address is configured", "Set DEV_REDIRECT_EMAIL, or add the Gmail credential")
    return Check("Sending mode", "ok", f"Dev: every email goes to {target}")


def _runs(db: Session) -> Check:
    stuck = db.scalar(
        select(func.count()).select_from(Run).where(
            Run.status.in_([RunStatus.pending, RunStatus.running]),
            func.coalesce(Run.heartbeat_at, Run.created_at) < _now() - timedelta(minutes=15),
        )
    ) or 0
    if stuck:
        return Check("Agent runs", "warn", f"{stuck} run(s) with no recent heartbeat", "They are marked failed the next time a run starts or is watched")
    running = db.scalar(select(func.count()).select_from(Run).where(Run.status == RunStatus.running)) or 0
    return Check("Agent runs", "ok", f"{running} running, none stalled")


def _reply_polling(db: Session) -> Check:
    if not get_settings().internal_task_token:
        return Check("Reply checks", "off", "The scheduled check is disabled", "Set INTERNAL_TASK_TOKEN to enable it")
    return Check("Reply checks", "ok", "Scheduled endpoint is enabled")


def _library(db: Session) -> Check:
    total = db.scalar(select(func.count()).select_from(Resume)) or 0
    if not total:
        return Check("CV library", "warn", "No CVs uploaded", "Upload one in Library before starting a run")
    return Check("CV library", "ok", f"{total} CV(s)")


def run_checks(db: Session) -> dict:
    """Every check, worst status first so a problem is the first thing on the panel."""
    checks = [_database(db), _storage(), _prompts(db), _sending(db), _runs(db), _reply_polling(db), _library(db)]
    for name in PROVIDERS:
        checks.append(_provider(db, name))
    gmail_age = _gmail_age(db)
    if gmail_age:
        checks.append(gmail_age)

    order = {"fail": 0, "warn": 1, "off": 2, "ok": 3}
    checks.sort(key=lambda c: (order.get(c.status, 9), c.name))
    worst = next((c.status for c in checks if c.status in ("fail", "warn")), "ok")
    return {"status": worst, "checked_at": _now().isoformat(), "checks": [c.__dict__ for c in checks]}

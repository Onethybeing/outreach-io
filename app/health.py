"""The Settings health panel: one place to see whether the parts this app depends on are working.

Each check answers "would this fail right now, and what would I do about it". Provider checks reuse
the same free calls the vault uses to test a key, so nothing here spends credits.
"""

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import vault
from app.config import get_settings
from app.contacts import sending, service
from app.discovery import graph as discovery
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
    key = "health/.probe"
    storage = get_storage()
    try:
        # Write, read back and delete: reading is its own permission, and a CV that can be uploaded
        # but not read back would otherwise look healthy.
        storage.save(key, b"ok", "text/plain")
        read_back = storage.read(key)
        storage.delete(key)
    except Exception as exc:  # noqa: BLE001 — any backend error is the same answer to the user
        logger.exception("Health: storage probe failed")
        return Check("File storage", "fail", f"{where}: {type(exc).__name__}", "CV uploads, attachments and sent-email copies will fail")
    if read_back != b"ok":
        return Check("File storage", "fail", f"{where}: read back the wrong bytes", "Attachments may be corrupt")
    return Check("File storage", "ok", f"{where}: write, read and delete all work")


def _credentials(db: Session, name: str) -> dict | Check:
    """Read one provider's key, or the Check explaining why we can't."""
    label = name.replace("_", " ").title()
    try:
        return vault.get_credential(db, name)
    except vault.VaultError:
        return Check(label, "off", "No key saved", "Add it in Settings > Vault")
    except RuntimeError as exc:
        # A rotated or mistyped VAULT_MASTER_KEY. Reporting this is the whole point of the panel,
        # so it must not be the thing that breaks it.
        return Check(label, "fail", str(exc), "Restore the previous VAULT_MASTER_KEY, or re-save the key")


def _test_provider(name: str, values: dict) -> Check:
    """Pure HTTP, no database, so this is safe to run in a worker thread."""
    label = name.replace("_", " ").title()
    try:
        ok, message = PROVIDERS[name].test(values)
    except Exception as exc:  # noqa: BLE001 — a provider being unreachable is a normal answer here
        return Check(label, "fail", f"Could not reach it ({type(exc).__name__})", "Try again, or check the provider's status page")
    return Check(label, "ok" if ok else "fail", message, None if ok else "Rotate the key in Settings > Vault")


def _providers(db: Session) -> list[Check]:
    """All the provider keys at once: six serial calls at 20s each could outlast the request."""
    ready: dict[str, dict] = {}
    checks: list[Check] = []
    for name in PROVIDERS:
        result = _credentials(db, name)
        if isinstance(result, Check):
            checks.append(result)
        else:
            ready[name] = result
    if not ready:
        return checks
    with ThreadPoolExecutor(max_workers=len(ready)) as pool:
        futures = {pool.submit(_test_provider, name, values): name for name, values in ready.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                checks.append(future.result())
            except Exception as exc:  # noqa: BLE001 — never let one provider break the panel
                logger.exception("Health: provider check for %s crashed", name)
                checks.append(Check(name.replace("_", " ").title(), "fail", f"Check failed ({type(exc).__name__})"))
    return checks


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
    # The same threshold the runner reclaims at, so the panel can't call a reclaimed run healthy.
    stuck = db.scalar(
        select(func.count()).select_from(Run).where(
            Run.status.in_([RunStatus.pending, RunStatus.running]),
            func.coalesce(Run.heartbeat_at, Run.created_at) < _now() - discovery.STALE_AFTER,
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


CACHE_SECONDS = 60
_cache: tuple[float, dict] | None = None


def _safely(name: str, check: Callable[[], Check]) -> Check:
    """A check that raises must still leave the panel usable."""
    try:
        return check()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Health: check %s crashed", name)
        return Check(name, "fail", f"The check itself failed ({type(exc).__name__})", "See the server logs")


def run_checks(db: Session, fresh: bool = False) -> dict:
    """Every check, worst status first so a problem is the first thing on the panel.

    Cached for a minute: the provider checks are real network calls, and the panel refetches on
    every mount.
    """
    global _cache
    if _cache and not fresh and _cache[0] > time.monotonic():
        return _cache[1]

    checks = [
        _safely("Database", lambda: _database(db)),
        _safely("File storage", _storage),
        _safely("Node prompts", lambda: _prompts(db)),
        _safely("Sending mode", lambda: _sending(db)),
        _safely("Agent runs", lambda: _runs(db)),
        _safely("Reply checks", lambda: _reply_polling(db)),
        _safely("CV library", lambda: _library(db)),
    ]
    checks.extend(_providers(db))
    # Skipped entirely when there is no gmail credential: the provider check already says so.
    gmail_age = _safely("Gmail token age", lambda: _gmail_age(db) or Check("Gmail token age", "off", ""))
    if gmail_age.detail:
        checks.append(gmail_age)

    order = {"fail": 0, "warn": 1, "off": 2, "ok": 3}
    checks.sort(key=lambda c: (order.get(c.status, 9), c.name))
    worst = next((c.status for c in checks if c.status in ("fail", "warn")), "ok")
    report = {"status": worst, "checked_at": _now().isoformat(), "checks": [c.__dict__ for c in checks]}
    _cache = (time.monotonic() + CACHE_SECONDS, report)
    return report

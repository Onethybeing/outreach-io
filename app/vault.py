"""Encrypted, versioned provider credentials (PLAN.md §7).

Full values never leave this module except through get_credential(), which only agent nodes call.
"""

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings, get_settings
from app.models import ApiCredential, CredentialStatus, User
from app.vault_providers import PROVIDERS

RETIRED_KEEP_DAYS = 7
CACHE_TTL_SECONDS = 60

# provider -> (expires_at monotonic, version, values). Per process; the TTL bounds how stale
# another Cloud Run instance can be after a rotation.
_cache: dict[str, tuple[float, int, dict]] = {}


class VaultError(Exception):
    """A user-facing problem: bad input, failed key test, missing credential, conflict."""


@dataclass
class ProviderStatus:
    provider: str
    configured: bool
    fields: tuple[str, ...]
    version: int | None = None
    last4: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    last_tested_at: datetime | None = None
    last_test_ok: bool | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fernet() -> Fernet:
    key = get_settings().vault_master_key
    if not key:
        raise RuntimeError("VAULT_MASTER_KEY is not set")
    return Fernet(key.encode())


def _encrypt(values: dict) -> str:
    return _fernet().encrypt(json.dumps(values).encode()).decode()


def _decrypt(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken:
        raise RuntimeError("Vault entry can't be decrypted — VAULT_MASTER_KEY changed?")


def _clean(provider: str, values: dict) -> dict:
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise VaultError(f"Unknown provider '{provider}'")
    unknown = set(values) - set(spec.fields)
    if unknown:
        raise VaultError(f"Unknown field(s) for {provider}: {', '.join(sorted(unknown))}")
    cleaned = {f: str(values.get(f) or "").strip() for f in spec.fields}
    missing = [f for f, v in cleaned.items() if not v]
    if missing:
        raise VaultError(f"Missing field(s) for {provider}: {', '.join(missing)}")
    return cleaned


def _last4(provider: str, values: dict) -> str:
    return values[PROVIDERS[provider].secret_field][-4:]


def _run_test(provider: str, values: dict) -> tuple[bool, str]:
    try:
        return PROVIDERS[provider].test(values)
    except httpx.HTTPError as exc:
        return False, f"Could not reach {provider}: {type(exc).__name__}"


def _active(db: Session, provider: str) -> ApiCredential | None:
    return db.scalar(
        select(ApiCredential).where(
            ApiCredential.provider == provider, ApiCredential.status == CredentialStatus.active
        )
    )


def _purge_retired(db: Session, provider: str) -> None:
    db.execute(
        delete(ApiCredential).where(
            ApiCredential.provider == provider,
            ApiCredential.status == CredentialStatus.retired,
            ApiCredential.retired_at < _now() - timedelta(days=RETIRED_KEEP_DAYS),
        )
    )


def _commit(db: Session, provider: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise VaultError(f"Another change to {provider} happened at the same time — retry")
    finally:
        _cache.pop(provider, None)


def list_status(db: Session) -> list[ProviderStatus]:
    active = {
        row.provider: row
        for row in db.scalars(
            select(ApiCredential).where(ApiCredential.status == CredentialStatus.active)
        )
    }
    creators = {
        u.id: u.email
        for u in db.scalars(select(User).where(User.id.in_([r.created_by for r in active.values()])))
    }
    statuses = []
    for provider, spec in PROVIDERS.items():
        row = active.get(provider)
        if row is None:
            statuses.append(ProviderStatus(provider, False, spec.fields))
            continue
        statuses.append(
            ProviderStatus(
                provider=provider,
                configured=True,
                fields=spec.fields,
                version=row.version,
                last4=row.last4,
                created_at=row.created_at,
                created_by=creators.get(row.created_by, "system (.env import)"),
                last_tested_at=row.last_tested_at,
                last_test_ok=row.last_test_ok,
            )
        )
    return statuses


def list_versions(db: Session, provider: str) -> list[ApiCredential]:
    if provider not in PROVIDERS:
        raise VaultError(f"Unknown provider '{provider}'")
    return list(
        db.scalars(
            select(ApiCredential)
            .where(ApiCredential.provider == provider)
            .order_by(ApiCredential.version.desc())
        )
    )


def save_credential(db: Session, provider: str, values: dict, user: User) -> str:
    """Create or rotate. The new value must pass a live test before it replaces the old one."""
    cleaned = _clean(provider, values)
    ok, message = _run_test(provider, cleaned)
    if not ok:
        audit.record(db, user, "vault.rotate_rejected", "provider", provider, {"reason": message})
        db.commit()
        raise VaultError(f"Key test failed, nothing changed: {message}")

    current = _active(db, provider)
    if current is not None:
        current.status = CredentialStatus.retired
        current.retired_at = _now()
        db.flush()  # clear the one-active-per-provider index before inserting the new row

    latest = db.scalar(
        select(func.max(ApiCredential.version)).where(ApiCredential.provider == provider)
    )
    new = ApiCredential(
        provider=provider,
        encrypted_value=_encrypt(cleaned),
        last4=_last4(provider, cleaned),
        version=(latest or 0) + 1,
        status=CredentialStatus.active,
        created_by=user.id,
        last_tested_at=_now(),
        last_test_ok=True,
    )
    db.add(new)
    _purge_retired(db, provider)
    audit.record(
        db,
        user,
        "vault.rotate" if current else "vault.create",
        "provider",
        provider,
        {"version": new.version, "last4": new.last4},
    )
    _commit(db, provider)
    return message


def test_active(db: Session, provider: str, user: User) -> tuple[bool, str]:
    row = _active(db, provider)
    if row is None:
        raise VaultError(f"{provider} is not configured")
    ok, message = _run_test(provider, _decrypt(row.encrypted_value))
    row.last_tested_at = _now()
    row.last_test_ok = ok
    audit.record(db, user, "vault.test", "provider", provider, {"ok": ok, "message": message})
    _commit(db, provider)
    return ok, message


def rollback(db: Session, provider: str, version: int, user: User) -> str:
    target = db.scalar(
        select(ApiCredential).where(
            ApiCredential.provider == provider, ApiCredential.version == version
        )
    )
    if target is None:
        raise VaultError(f"{provider} version {version} not found (older than {RETIRED_KEEP_DAYS} days?)")
    if target.status == CredentialStatus.active:
        raise VaultError(f"{provider} version {version} is already active")

    ok, message = _run_test(provider, _decrypt(target.encrypted_value))
    if not ok:
        audit.record(
            db, user, "vault.rollback_rejected", "provider", provider,
            {"version": version, "reason": message},
        )
        db.commit()
        raise VaultError(f"Old key failed its test, nothing changed: {message}")

    current = _active(db, provider)
    if current is not None:
        current.status = CredentialStatus.retired
        current.retired_at = _now()
        db.flush()
    target.status = CredentialStatus.active
    target.retired_at = None
    target.last_tested_at = _now()
    target.last_test_ok = True
    audit.record(
        db, user, "vault.rollback", "provider", provider,
        {"to_version": version, "from_version": current.version if current else None},
    )
    _commit(db, provider)
    return message


def get_credential(db: Session, provider: str) -> dict:
    """Decrypted values for agent nodes. Raises VaultError if the provider isn't configured."""
    cached = _cache.get(provider)
    if cached and cached[0] > time.monotonic():
        return dict(cached[2])
    row = _active(db, provider)
    if row is None:
        raise VaultError(f"{provider} is not configured — add it in Settings → Vault")
    values = _decrypt(row.encrypted_value)
    _cache[provider] = (time.monotonic() + CACHE_TTL_SECONDS, row.version, values)
    return dict(values)


def clear_cache() -> None:
    _cache.clear()


def _env_values(settings: Settings) -> dict[str, dict]:
    return {
        "groq": {"api_key": settings.groq_api_key},
        "tavily": {"api_key": settings.tavily_api_key},
        "apollo": {"api_key": settings.apollo_api_key},
        "brightdata": {"api_key": settings.brightdata_api_key},
        "langfuse": {
            "public_key": settings.langfuse_public_key,
            "secret_key": settings.langfuse_secret_key,
            "host": settings.langfuse_host,
        },
        "gmail": {
            "client_id": settings.gmail_client_id,
            "client_secret": settings.gmail_client_secret,
            "refresh_token": settings.gmail_refresh_token,
            "sender_address": settings.gmail_sender_address,
        },
    }


def seed_from_env(db: Session, settings: Settings) -> list[str]:
    """Import .env keys for providers that have never had a vault entry. Untested; returns names."""
    seeded = []
    for provider, values in _env_values(settings).items():
        if not all(v.strip() for v in values.values()):
            continue
        has_any = db.scalar(
            select(func.count()).select_from(ApiCredential).where(ApiCredential.provider == provider)
        )
        if has_any:
            continue
        cleaned = _clean(provider, values)
        db.add(
            ApiCredential(
                provider=provider,
                encrypted_value=_encrypt(cleaned),
                last4=_last4(provider, cleaned),
                version=1,
                status=CredentialStatus.active,
            )
        )
        audit.record(db, None, "vault.seed", "provider", provider, {"source": ".env"})
        seeded.append(provider)
    db.commit()
    return seeded

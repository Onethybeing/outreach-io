"""People who must never be contacted again.

Kept as a hash of the LinkedIn URL rather than the person's details, so the record outlives an
erased contact without storing anything identifying. Set when someone unsubscribes, when an
operator marks a contact do-not-contact, and when a contact is erased on request.
"""

import hashlib

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import ContactSuppression, User


def fingerprint(linkedin_url: str) -> str:
    return hashlib.sha256(linkedin_url.strip().lower().encode()).hexdigest()


def suppressed(db: Session, linkedin_urls: list[str]) -> set[str]:
    """The subset of `linkedin_urls` that must not be contacted."""
    if not linkedin_urls:
        return set()
    by_hash = {fingerprint(url): url for url in linkedin_urls}
    hits = db.scalars(select(ContactSuppression.linkedin_hash).where(ContactSuppression.linkedin_hash.in_(by_hash)))
    return {by_hash[digest] for digest in hits}


def is_suppressed(db: Session, linkedin_url: str) -> bool:
    return bool(suppressed(db, [linkedin_url]))


def suppress(db: Session, linkedin_url: str, reason: str, user: User | None = None) -> None:
    """Idempotent; the caller commits."""
    digest = fingerprint(linkedin_url)
    if db.scalar(select(ContactSuppression.id).where(ContactSuppression.linkedin_hash == digest)):
        return
    db.add(ContactSuppression(linkedin_hash=digest, reason=reason, created_by=user.id if user else None))


def unsuppress(db: Session, linkedin_url: str) -> None:
    """Undo a block — e.g. do-not-contact was set on the wrong person. The caller commits."""
    db.execute(delete(ContactSuppression).where(ContactSuppression.linkedin_hash == fingerprint(linkedin_url)))

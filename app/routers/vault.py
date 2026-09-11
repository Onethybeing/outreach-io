from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import vault
from app.auth import require
from app.db import get_db
from app.models import ApiCredential, User
from app.schemas import CredentialIn, CredentialVersionOut, ProviderStatusOut, TestResultOut

# Every route is admin-only, and none returns a decrypted value.
router = APIRouter(prefix="/vault", tags=["vault"])


@router.get("", response_model=list[ProviderStatusOut])
def list_providers(
    db: Session = Depends(get_db), _: User = Depends(require("vault.manage"))
) -> list[vault.ProviderStatus]:
    return vault.list_status(db)


@router.get("/{provider}/versions", response_model=list[CredentialVersionOut])
def list_versions(
    provider: str, db: Session = Depends(get_db), _: User = Depends(require("vault.manage"))
) -> list[ApiCredential]:
    return vault.list_versions(db, provider)


@router.put("/{provider}", response_model=TestResultOut)
def save(
    provider: str,
    body: CredentialIn,
    db: Session = Depends(get_db),
    user: User = Depends(require("vault.manage")),
) -> TestResultOut:
    return TestResultOut(ok=True, message=vault.save_credential(db, provider, body.values, user))


@router.post("/{provider}/test", response_model=TestResultOut)
def test(
    provider: str, db: Session = Depends(get_db), user: User = Depends(require("vault.manage"))
) -> TestResultOut:
    ok, message = vault.test_active(db, provider, user)
    return TestResultOut(ok=ok, message=message)


@router.post("/{provider}/rollback/{version}", response_model=TestResultOut)
def rollback(
    provider: str,
    version: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("vault.manage")),
) -> TestResultOut:
    return TestResultOut(ok=True, message=vault.rollback(db, provider, version, user))

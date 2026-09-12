"""The health panel exists to report breakage, so its own failure modes matter most."""

import pytest

from app import health, vault
from app.models import UserRole
from app.vault_providers import PROVIDERS


@pytest.fixture(autouse=True)
def no_cache():
    health._cache = None
    yield
    health._cache = None


def _by_name(report: dict) -> dict[str, dict]:
    return {c["name"]: c for c in report["checks"]}


def test_report_covers_every_provider_and_sorts_problems_first(db, make_user, login):
    admin = login(make_user(UserRole.admin))
    response = admin.get("/settings/health")
    assert response.status_code == 200, response.text
    report = response.json()

    checks = _by_name(report)
    for provider in PROVIDERS:
        assert provider.replace("_", " ").title() in checks
    assert {"Database", "File storage", "Node prompts", "Sending mode"} <= set(checks)
    order = {"fail": 0, "warn": 1, "off": 2, "ok": 3}
    statuses = [order[c["status"]] for c in report["checks"]]
    assert statuses == sorted(statuses)


def test_an_undecryptable_key_is_reported_not_a_500(db, make_user, login, monkeypatch):
    """A changed VAULT_MASTER_KEY raises RuntimeError. The panel's whole job is to say so."""

    def cannot_decrypt(db_, provider):
        raise RuntimeError("Vault entry can't be decrypted. Did VAULT_MASTER_KEY change?")

    monkeypatch.setattr(vault, "get_credential", cannot_decrypt)
    response = login(make_user(UserRole.admin)).get("/settings/health")
    assert response.status_code == 200, response.text
    groq = _by_name(response.json())["Groq"]
    assert groq["status"] == "fail" and "decrypted" in groq["detail"]


def test_a_check_that_raises_does_not_break_the_panel(db, make_user, login, monkeypatch):
    monkeypatch.setattr(health, "_storage", lambda: 1 / 0)
    response = login(make_user(UserRole.admin)).get("/settings/health")
    assert response.status_code == 200
    storage = _by_name(response.json())["File storage"]
    assert storage["status"] == "fail" and "ZeroDivisionError" in storage["detail"]


def test_results_are_cached_then_refreshed_on_demand(db, make_user, login, monkeypatch):
    calls = {"n": 0}

    def counted(db_):
        calls["n"] += 1
        return []

    monkeypatch.setattr(health, "_providers", counted)
    admin = login(make_user(UserRole.admin))
    admin.get("/settings/health")
    admin.get("/settings/health")
    assert calls["n"] == 1, "the provider calls are real network calls, so repeat views reuse them"
    admin.get("/settings/health?fresh=true")
    assert calls["n"] == 2


def test_the_panel_is_admin_only(db, make_user, login):
    assert login(make_user(UserRole.operator)).get("/settings/health").status_code == 403

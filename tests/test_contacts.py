import uuid

import httpx
import pytest
from sqlalchemy import select

from app import llm, telemetry
from app.contacts import apollo, brightdata, service, verification
from app.contacts.brightdata import Profile
from app.models import (
    AuditLog, Candidate, CandidateStatus, Contact, EmailLookupStatus, Resume, Run, RunStatus, SendStatus,
    Startup, UserRole, VerificationStatus,
)
from app.routers import contacts as contacts_router

POWERFUL = Profile(
    name="Simon", current_company="Powerful Medical", current_company_slug="powerful-medical",
    current_company_url="https://www.linkedin.com/company/powerful-medical", position="CTO",
)


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    monkeypatch.setattr(telemetry, "langfuse_client", lambda db: None)


@pytest.fixture
def world(db, monkeypatch):
    """A completed run with one startup and pending candidates; providers faked."""
    resume = Resume(filename="cv.pdf", storage_path="x.pdf")
    db.add(resume)
    db.flush()
    run = Run(resume_id=resume.id, status=RunStatus.completed)
    db.add(run)
    db.flush()
    startup = Startup(run_id=run.id, name="Powerful Medical", website="https://powerfulmedical.com")
    db.add(startup)
    db.flush()
    candidates = [
        Candidate(run_id=run.id, startup_id=startup.id, name=f"Person {i}", title="CTO",
                  linkedin_url=f"https://www.linkedin.com/in/person-{uuid.uuid4().hex[:8]}")
        for i in range(3)
    ]
    db.add_all(candidates)
    db.commit()

    state = {"profile": POWERFUL, "org_calls": 0, "profile_calls": 0, "email": apollo.EmailResult(True, "simon@powerfulmedical.com", "Found by Apollo")}

    def fake_enrich(db_, website, name=None):
        state["org_calls"] += 1
        return {"name": "Powerful Medical", "linkedin_url": "http://www.linkedin.com/company/powerful-medical"}

    def fake_profile(db_, url):
        state["profile_calls"] += 1
        if isinstance(state["profile"], Exception):
            raise state["profile"]
        return state["profile"]

    def fake_email(db_, name, url, website):
        if isinstance(state["email"], Exception):
            raise state["email"]
        return state["email"]

    monkeypatch.setattr(apollo, "enrich_org", fake_enrich)
    monkeypatch.setattr(brightdata, "scrape_profile", fake_profile)
    monkeypatch.setitem(service.EMAIL_PROVIDERS, "apollo", fake_email)
    # Jobs run inline against the test transaction.
    monkeypatch.setattr(verification, "submit_verification", lambda cid: verification.execute_verification(db, cid))
    monkeypatch.setattr(contacts_router.verification, "submit_verification", lambda cid: verification.execute_verification(db, cid))
    return {"resume": resume, "run": run, "startup": startup, "candidates": candidates, "state": state}


@pytest.fixture
def operator(make_user, login):
    return login(make_user(UserRole.operator))


def _approve(client, candidate):
    response = client.post(f"/candidates/{candidate.id}/approve")
    assert response.status_code == 200, response.text
    return response.json()["contact"]


# --- decisions -------------------------------------------------------------------------

def test_approve_creates_contact_and_verifies_by_company_page(db, world, operator):
    candidate = world["candidates"][0]
    contact = _approve(operator, candidate)
    assert contact["cv_used_id"] == str(world["resume"].id) and contact["cv_filename"] == "cv.pdf"

    detail = operator.get(f"/contacts/{contact['id']}").json()
    assert detail["verification_status"] == "verified"
    assert "LinkedIn page matches" in detail["verification_note"]
    assert detail["company_at_scrape"] == "Powerful Medical" and detail["verified_title"] == "CTO"
    assert detail["employment_verified"] is True

    db.refresh(candidate)
    assert candidate.status == CandidateStatus.approved and str(candidate.contact_id) == contact["id"]
    assert db.scalar(select(AuditLog).where(AuditLog.action == "candidates.approve")) is not None


def test_company_lookup_happens_once_per_startup(world, operator):
    _approve(operator, world["candidates"][0])
    _approve(operator, world["candidates"][1])
    assert world["state"]["org_calls"] == 1 and world["state"]["profile_calls"] == 2


def test_decisions_are_one_shot_and_need_completed_run(db, world, operator):
    candidate = world["candidates"][0]
    _approve(operator, candidate)
    assert operator.post(f"/candidates/{candidate.id}/approve").status_code == 409
    assert operator.post(f"/candidates/{candidate.id}/reject").status_code == 409

    other = world["candidates"][1]
    world["run"].status = RunStatus.running
    db.commit()
    assert operator.post(f"/candidates/{other.id}/approve").status_code == 409


def test_viewer_cannot_decide(world, make_user, login):
    viewer = login(make_user(UserRole.viewer))
    assert viewer.post(f"/candidates/{world['candidates'][0].id}/approve").status_code == 403


def test_person_approved_elsewhere_must_be_reused_or_updated(db, world, operator):
    first = world["candidates"][0]
    contact = _approve(operator, first)
    # Same person proposed again by a later run.
    later_run = Run(resume_id=world["resume"].id, status=RunStatus.completed)
    db.add(later_run)
    db.flush()
    again = Candidate(run_id=later_run.id, startup_id=world["startup"].id, name="Person 0", linkedin_url=first.linkedin_url)
    db.add(again)
    db.commit()

    response = operator.post(f"/candidates/{again.id}/approve")
    assert response.status_code == 409 and "Reuse or Update" in response.json()["detail"]
    db.refresh(again)
    assert str(again.existing_contact_id) == contact["id"]  # flagged at decision time

    reused = operator.post(f"/candidates/{again.id}/reuse").json()
    assert reused["status"] == "reused" and reused["contact"]["id"] == contact["id"]


def test_update_repoints_contact_and_resets_email_but_keeps_history(db, world, operator):
    first = world["candidates"][0]
    contact_id = uuid.UUID(_approve(operator, first)["id"])
    contact = db.get(Contact, contact_id)
    contact.email, contact.email_source, contact.email_lookup_status = "old@powerfulmedical.com", "manual", EmailLookupStatus.found
    contact.send_status, contact.do_not_contact = SendStatus.sent_dev, True
    db.commit()

    new_resume = Resume(filename="cv2.pdf", storage_path="y.pdf")
    db.add(new_resume)
    db.flush()
    new_run = Run(resume_id=new_resume.id, status=RunStatus.completed)
    db.add(new_run)
    db.flush()
    new_startup = Startup(run_id=new_run.id, name="Nova Robotics", website="https://novarobotics.example")
    db.add(new_startup)
    db.flush()
    again = Candidate(run_id=new_run.id, startup_id=new_startup.id, name="Person 0", title="VP Eng", linkedin_url=first.linkedin_url)
    db.add(again)
    db.commit()
    world["state"]["profile"] = Profile("Person 0", "Nova Robotics", None, None, "VP Eng")

    body = operator.post(f"/candidates/{again.id}/update-contact").json()["contact"]
    assert body["startup_name"] == "Nova Robotics" and body["cv_filename"] == "cv2.pdf" and body["title"] == "VP Eng"
    assert body["verification_status"] == "verified"  # re-verified against the new startup (by name)
    assert body["email"] is None and body["email_lookup_status"] == "not_run"
    assert body["send_status"] == "sent_dev" and body["do_not_contact"] is True


def test_bulk_decisions_report_each_result(world, operator):
    ids = [str(c.id) for c in world["candidates"][:2]]
    results = operator.post("/candidates/bulk", json={"action": "reject", "candidate_ids": ids + [ids[0]]}).json()
    assert [r["status"] for r in results] == ["rejected", "rejected"]
    again = operator.post("/candidates/bulk", json={"action": "approve", "candidate_ids": ids}).json()
    assert all(not r["ok"] and "already rejected" in r["error"] for r in again)


# --- verification decision table ------------------------------------------------------

def _decide(profile, slug="powerful-medical", verdict=None):
    calls = []

    def tiebreak():
        calls.append(1)
        return verdict or {}

    return verification.decide(profile, "Powerful Medical", "https://powerfulmedical.com", slug, tiebreak), calls


@pytest.mark.parametrize("profile, slug, verdict, expected, llm_called", [
    (Profile("x", None, None, None, None), "powerful-medical", None, VerificationStatus.unconfirmed, False),
    (POWERFUL, "powerful-medical", None, VerificationStatus.verified, False),
    (Profile("x", "Powerful Medical Inc", "pm-inc", None, None), "powerful-medical", None, VerificationStatus.verified, False),
    (Profile("x", "Other Corp", "other-corp", None, None), "powerful-medical", None, VerificationStatus.mismatch, False),
    (Profile("x", "PM Health", None, None, None), None, {"match": True, "confidence": 0.9, "reason": "rebrand"}, VerificationStatus.verified, True),
    (Profile("x", "PM Health", None, None, None), None, {"match": True, "confidence": 0.4}, VerificationStatus.unconfirmed, True),
    (Profile("x", "Other Corp", None, None, None), None, {"match": False, "confidence": 0.95, "reason": "different"}, VerificationStatus.mismatch, True),
])
def test_verification_decisions(profile, slug, verdict, expected, llm_called):
    decision, calls = _decide(profile, slug, verdict)
    assert decision.status == expected
    assert bool(calls) is llm_called


def test_tiebreak_uses_cross_check_prompt(db, world, operator, monkeypatch):
    from app import prompts

    prompts.seed_defaults(db)
    world["state"]["profile"] = Profile("x", "PM Health", None, None, "CTO")
    monkeypatch.setattr(apollo, "enrich_org", lambda db_, w, n=None: None)  # no company page known
    seen = {}

    def fake_llm(db_, model, prompt, temperature, max_tokens=4096, **kwargs):
        seen.update(name=kwargs["name"], prompt=prompt)
        return {"match": False, "confidence": 0.9, "reason": "different company"}

    monkeypatch.setattr(llm, "complete_json", fake_llm)
    contact = _approve(operator, world["candidates"][0])
    detail = operator.get(f"/contacts/{contact['id']}").json()
    assert detail["verification_status"] == "mismatch"
    assert seen["name"] == "cross_check_company_match" and "PM Health" in seen["prompt"]


def test_scrape_failure_is_retryable(db, world, operator):
    world["state"]["profile"] = brightdata.ProfileError("BrightData refused the scrape request (HTTP 402)")
    contact = _approve(operator, world["candidates"][0])
    detail = operator.get(f"/contacts/{contact['id']}").json()
    assert detail["verification_status"] == "failed" and "402" in detail["verification_note"]

    world["state"]["profile"] = POWERFUL
    assert operator.post(f"/contacts/{contact['id']}/verify").status_code == 202
    assert operator.get(f"/contacts/{contact['id']}").json()["verification_status"] == "verified"


def test_verify_refuses_while_running(db, world, operator, monkeypatch):
    monkeypatch.setattr(contacts_router.verification, "submit_verification", lambda cid: None)
    contact = _approve(operator, world["candidates"][0])  # stays queued
    assert operator.post(f"/contacts/{contact['id']}/verify").status_code == 409


def test_restart_fails_interrupted_jobs(db, world, operator, monkeypatch):
    monkeypatch.setattr(contacts_router.verification, "submit_verification", lambda cid: None)
    contact_id = uuid.UUID(_approve(operator, world["candidates"][0])["id"])
    assert verification.fail_interrupted_jobs(db) >= 1
    contact = db.get(Contact, contact_id)
    db.refresh(contact)
    assert contact.verification_status == VerificationStatus.failed and "restart" in contact.verification_note


# --- email ------------------------------------------------------------------------------

def _verified_contact(operator, world, index=0):
    return _approve(operator, world["candidates"][index])["id"]


def test_email_lookup_rules_and_success(db, world, operator):
    contact_id = _verified_contact(operator, world)
    found = operator.post(f"/contacts/{contact_id}/email/lookup").json()
    assert found["email"] == "simon@powerfulmedical.com" and found["email_source"] == "apollo"
    assert found["email_lookup_status"] == "found"
    again = operator.post(f"/contacts/{contact_id}/email/lookup")
    assert again.status_code == 409 and "force" in again.json()["detail"]
    assert operator.post(f"/contacts/{contact_id}/email/lookup?force=true").status_code == 200


def test_email_lookup_needs_verification_unless_forced(db, world, operator):
    world["state"]["profile"] = Profile("x", "Other Corp", "other-corp", None, None)
    contact_id = _verified_contact(operator, world)  # → mismatch
    blocked = operator.post(f"/contacts/{contact_id}/email/lookup")
    assert blocked.status_code == 400 and "verify" in blocked.json()["detail"]


def test_provider_unavailable_is_clear_and_marks_failed(db, world, operator):
    world["state"]["email"] = apollo.ProviderUnavailable("Apollo's current plan doesn't include email lookup.")
    contact_id = _verified_contact(operator, world)
    response = operator.post(f"/contacts/{contact_id}/email/lookup")
    assert response.status_code == 400 and "plan" in response.json()["detail"]
    detail = operator.get(f"/contacts/{contact_id}").json()
    assert detail["email_lookup_status"] == "failed"
    ids = {c["id"] for c in operator.get("/contacts?view=no_email").json()}
    assert contact_id in ids


def test_not_found_goes_to_no_email_then_manual_entry_fixes_it(db, world, operator):
    world["state"]["email"] = apollo.EmailResult(False, None, "Apollo has no email")
    contact_id = _verified_contact(operator, world)
    operator.post(f"/contacts/{contact_id}/email/lookup")
    assert contact_id in {c["id"] for c in operator.get("/contacts?view=no_email").json()}
    assert contact_id not in {c["id"] for c in operator.get("/contacts?view=active").json()}

    assert operator.put(f"/contacts/{contact_id}/email", json={"email": "nope"}).status_code == 422
    manual = operator.put(f"/contacts/{contact_id}/email", json={"email": " Simon@PowerfulMedical.com "}).json()
    assert manual["email"] == "simon@powerfulmedical.com" and manual["email_source"] == "manual"
    assert contact_id in {c["id"] for c in operator.get("/contacts?view=active").json()}


def test_do_not_contact_blocks_lookup(db, world, operator):
    contact_id = _verified_contact(operator, world)
    contact = db.get(Contact, uuid.UUID(contact_id))
    contact.do_not_contact = True
    db.commit()
    assert operator.post(f"/contacts/{contact_id}/email/lookup?force=true").status_code == 400


def test_bulk_lookup_dry_run_counts_only_eligible(db, world, operator, monkeypatch):
    submitted = []
    monkeypatch.setattr(service, "submit_bulk_lookup", lambda ids, uid: submitted.append(ids))
    ids = [_verified_contact(operator, world, i) for i in range(3)]
    operator.put(f"/contacts/{ids[0]}/email", json={"email": "a@powerfulmedical.com"})  # already has email
    mismatch = db.get(Contact, uuid.UUID(ids[1]))
    mismatch.verification_status = VerificationStatus.mismatch
    db.commit()

    dry = operator.post("/contacts/email/lookup-bulk", json={}).json()
    assert dry["eligible"] >= 1 and dry["queued"] is False and submitted == []
    scoped = operator.post("/contacts/email/lookup-bulk", json={"dry_run": False, "contact_ids": ids}).json()
    assert scoped == {"provider": "apollo", "eligible": 1, "queued": True}
    assert submitted == [[uuid.UUID(ids[2])]]


def test_bulk_job_stops_when_provider_unavailable(db, world, operator):
    ids = [uuid.UUID(_verified_contact(operator, world, i)) for i in range(2)]
    world["state"]["email"] = apollo.ProviderUnavailable("plan doesn't include email lookup")
    admin = db.scalar(select(Contact.approved_by).where(Contact.id == ids[0]))
    service.execute_bulk_lookup(db, ids, admin)
    statuses = [db.get(Contact, i).email_lookup_status for i in ids]
    assert statuses.count(EmailLookupStatus.failed) == 1 and statuses.count(EmailLookupStatus.not_run) == 1


def test_email_provider_setting_is_admin_only(make_user, login):
    assert login(make_user(UserRole.operator)).put("/settings/email-provider", json={"provider": "apollo"}).status_code == 403
    admin = login(make_user(UserRole.admin))
    assert admin.put("/settings/email-provider", json={"provider": "nope"}).status_code == 400
    assert admin.put("/settings/email-provider", json={"provider": "apollo"}).json()["email_provider"] == "apollo"


# --- provider clients --------------------------------------------------------------------

def test_apollo_find_email_parsing(db, monkeypatch):
    from app import vault

    monkeypatch.setattr(vault, "get_credential", lambda db_, p: {"api_key": "k"})
    responses = {
        "plan": httpx.Response(403, json={"error": "not included in your Free plan", "error_code": "API_INACCESSIBLE"}),
        "found": httpx.Response(200, json={"person": {"email": "Jane@Acme.com", "email_status": "verified"}}),
        "locked": httpx.Response(200, json={"person": {"email": "email_not_unlocked@domain.com", "email_status": None}}),
        "nobody": httpx.Response(200, json={"person": None}),
    }
    current = {}
    monkeypatch.setattr(apollo.httpx, "post", lambda *a, **k: responses[current["case"]])

    current["case"] = "plan"
    with pytest.raises(apollo.ProviderUnavailable, match="plan"):
        apollo.find_email(db, "Jane", "https://www.linkedin.com/in/jane", "acme.com")
    current["case"] = "found"
    assert apollo.find_email(db, "Jane", "u", "acme.com").email == "jane@acme.com"
    current["case"] = "locked"
    assert apollo.find_email(db, "Jane", "u", "acme.com").found is False
    current["case"] = "nobody"
    assert "no record" in apollo.find_email(db, "Jane", "u", "acme.com").note


def test_apollo_enrich_by_name_only_trusts_matching_company(db, monkeypatch):
    from app import vault

    monkeypatch.setattr(vault, "get_credential", lambda db_, p: {"api_key": "k"})
    seen = []

    def fake_get(url, params=None, **kwargs):
        seen.append(params)
        return httpx.Response(200, json={"organization": {"name": "Powerful Medical", "linkedin_url": "http://www.linkedin.com/company/powerful-medical", "website_url": "http://www.powerfulmedical.com"}})

    monkeypatch.setattr(apollo.httpx, "get", fake_get)
    assert apollo.enrich_org(db, "https://powerfulmedical.com/x", "ignored")["name"] == "Powerful Medical"
    assert apollo.enrich_org(db, None, "Powerful Medical")["website_url"] == "http://www.powerfulmedical.com"
    assert apollo.enrich_org(db, None, "Powerful Medicine Labs") is None  # same-name lookalike not trusted
    assert apollo.enrich_org(db, None, None) is None
    assert seen == [{"domain": "powerfulmedical.com"}, {"name": "Powerful Medical"}, {"name": "Powerful Medicine Labs"}]


def test_brightdata_scrape_trigger_poll_download(db, monkeypatch):
    from app import vault

    monkeypatch.setattr(vault, "get_credential", lambda db_, p: {"api_key": "k"})
    monkeypatch.setattr(brightdata.time, "sleep", lambda s: None)
    progress = iter(["running", "running", "ready"])
    monkeypatch.setattr(brightdata.httpx, "post", lambda *a, **k: httpx.Response(200, json={"snapshot_id": "sd_1"}))

    def fake_get(url, **kwargs):
        if "/progress/" in url:
            return httpx.Response(200, json={"status": next(progress)})
        return httpx.Response(200, json=[{
            "name": "Simon Rovder", "position": None,
            "current_company": {"name": "Powerful Medical", "company_id": "powerful-medical",
                                "link": "https://www.linkedin.com/company/powerful-medical?trk=x"},
        }])

    monkeypatch.setattr(brightdata.httpx, "get", fake_get)
    profile = brightdata.scrape_profile(db, "https://www.linkedin.com/in/simon")
    assert (profile.current_company, profile.current_company_slug) == ("Powerful Medical", "powerful-medical")

    monkeypatch.setattr(brightdata.httpx, "post", lambda *a, **k: httpx.Response(401))
    with pytest.raises(brightdata.ProfileError, match="API key"):
        brightdata.scrape_profile(db, "https://www.linkedin.com/in/simon")

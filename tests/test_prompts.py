import pytest
from sqlalchemy import func, select

from app import llm, prompts
from app.models import AuditLog, Prompt, UserRole
from app.prompt_defaults import NODES


@pytest.fixture(autouse=True)
def seeded(db):
    prompts.seed_defaults(db)  # no-op if the real DB already has them


@pytest.fixture
def admin_client(make_user, login):
    return login(make_user(UserRole.admin))


def _active_version(db, node):
    return db.scalar(select(Prompt.version).where(Prompt.node_name == node, Prompt.is_active))


def test_lists_every_node_with_an_active_prompt(admin_client):
    nodes = admin_client.get("/prompts").json()
    assert {n["node"] for n in nodes} == set(NODES)
    assert all(n["active"] is not None for n in nodes)


def test_invalid_edit_is_rejected_and_nothing_saved(db, admin_client):
    before = db.scalar(select(func.count()).select_from(Prompt))
    response = admin_client.post(
        "/prompts/generate_draft/versions",
        json={"template": "Hi {{ contact_name }}", "model": "openai/gpt-oss-120b", "temperature": 0.5},
    )
    assert response.status_code == 400
    assert "candidate_profile" in response.json()["detail"]
    assert db.scalar(select(func.count()).select_from(Prompt)) == before


def test_save_without_activate_keeps_current_active(db, admin_client):
    active = _active_version(db, "classify_reply")
    template = NODES["classify_reply"].template + "\nBe brief."
    created = admin_client.post(
        "/prompts/classify_reply/versions",
        json={"template": template, "model": "openai/gpt-oss-20b", "temperature": 0},
    ).json()
    assert created["version"] == active + 1 and created["is_active"] is False
    assert _active_version(db, "classify_reply") == active


def test_save_and_activate_then_roll_back(db, admin_client):
    original = _active_version(db, "classify_reply")
    new = admin_client.post(
        "/prompts/classify_reply/versions",
        json={"template": NODES["classify_reply"].template + "\nV2", "model": "openai/gpt-oss-20b",
              "temperature": 0, "note": "try shorter", "activate": True},
    ).json()
    assert _active_version(db, "classify_reply") == new["version"]
    assert db.scalar(select(AuditLog).where(AuditLog.action == "prompts.activate")) is not None

    assert admin_client.post(f"/prompts/classify_reply/activate/{original}").status_code == 200
    assert _active_version(db, "classify_reply") == original
    active_count = db.scalar(
        select(func.count()).select_from(Prompt).where(Prompt.node_name == "classify_reply", Prompt.is_active)
    )
    assert active_count == 1


def test_diff_shows_changed_lines(admin_client, db):
    base = _active_version(db, "classify_reply")
    new = admin_client.post(
        "/prompts/classify_reply/versions",
        json={"template": NODES["classify_reply"].template + "\nEXTRA LINE", "model": "openai/gpt-oss-120b", "temperature": 0},
    ).json()["version"]
    diff = admin_client.get(f"/prompts/classify_reply/diff?a={base}&b={new}").json()["diff"]
    assert "+EXTRA LINE" in diff
    assert "+model: openai/gpt-oss-120b" in diff


def test_reset_to_default_creates_new_active_version(db, admin_client):
    admin_client.post(
        "/prompts/find_kdms/versions",
        json={"template": NODES["find_kdms"].template + "\nchanged", "model": "x", "temperature": 0, "activate": True},
    )
    reset = admin_client.post("/prompts/find_kdms/reset").json()
    assert reset["is_active"] and reset["template"] == NODES["find_kdms"].template
    assert reset["note"] == "Reset to default"


def test_test_on_sample_runs_llm_and_writes_nothing(db, admin_client, monkeypatch):
    seen = {}

    def fake_complete(_db, model, prompt, temperature, max_tokens=2048):
        seen.update(model=model, prompt=prompt)
        return '{"label": "out_of_office"}'

    monkeypatch.setattr(llm, "complete", fake_complete)
    before = db.scalar(select(func.count()).select_from(Prompt))
    response = admin_client.post(
        "/prompts/classify_reply/test",
        json={"template": NODES["classify_reply"].template, "model": "openai/gpt-oss-20b",
              "temperature": 0, "variables": {"reply_text": "I am away until June."}},
    ).json()
    assert response["output"] == '{"label": "out_of_office"}'
    assert "I am away until June." in response["rendered_prompt"] == seen["prompt"]
    assert db.scalar(select(func.count()).select_from(Prompt)) == before


def test_optional_variables_may_be_missing_at_render_time():
    rendered = prompts.render("generate_draft", NODES["generate_draft"].template, {
        "candidate_profile": "{}", "contact_name": "Jane", "startup_name": "Acme",
    })
    assert "Sign off as the candidate." in rendered


def test_seed_is_idempotent(db):
    assert prompts.seed_defaults(db) == []


@pytest.mark.parametrize(
    "template, message",
    [
        ("{{ reply_text }} {{ other }}", "Unknown variable"),
        ("{{ reply_text ", "syntax error"),
        ("{{ reply_text }}{{ ''.__class__.__mro__ }}", "unsafe"),
        ("   ", "empty"),
    ],
)
def test_validation_messages(template, message):
    with pytest.raises(prompts.PromptError, match=message):
        prompts.validate("classify_reply", template)

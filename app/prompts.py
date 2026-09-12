"""Versioned, editable node prompts (PLAN.md §8). Saving never overwrites; activating is rollback."""

import difflib

from jinja2 import StrictUndefined, TemplateSyntaxError, meta
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, llm
from app.models import AuditLog, Prompt, User
from app.prompt_defaults import NODES, NodeContract

# Sandboxed: prompts are edited in the dashboard, so templates must not reach Python internals.
_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


class PromptError(Exception):
    """A user-facing problem: invalid template, unknown node/version, conflict."""


def _contract(node: str) -> NodeContract:
    contract = NODES.get(node)
    if contract is None:
        raise PromptError(f"Unknown node '{node}'")
    return contract


def validate(node: str, template: str) -> str:
    """Check variables and render against sample data. Returns the rendered sample."""
    contract = _contract(node)
    if not template.strip():
        raise PromptError("Template is empty")
    try:
        parsed = _env.parse(template)
    except TemplateSyntaxError as exc:
        raise PromptError(f"Template syntax error on line {exc.lineno}: {exc.message}")

    used = meta.find_undeclared_variables(parsed)
    missing = [v for v in contract.required if v not in used]
    if missing:
        raise PromptError(f"Template must use: {', '.join('{{ ' + v + ' }}' for v in missing)}")
    unknown = sorted(used - set(contract.variables))
    if unknown:
        raise PromptError(
            f"Unknown variable(s): {', '.join(unknown)}. Available: {', '.join(contract.variables)}"
        )
    # Must work whether or not the node has values for the optional variables.
    render(node, template, {v: contract.sample[v] for v in contract.required})
    return render(node, template, contract.sample)


def render(node: str, template: str, variables: dict) -> str:
    """Optional variables the caller didn't supply render as empty text."""
    contract = _contract(node)
    values = {v: "" for v in contract.optional} | variables
    try:
        return _env.from_string(template).render(**values)
    except Exception as exc:  # noqa: BLE001: any render failure is a template problem to show the editor
        raise PromptError(f"Template failed to render: {exc}")


def _check_settings(model: str, temperature: float) -> None:
    if not model.strip():
        raise PromptError("Model is required")
    if not 0 <= temperature <= 2:
        raise PromptError("Temperature must be between 0 and 2")


def get_active(db: Session, node: str) -> Prompt:
    _contract(node)
    prompt = db.scalar(select(Prompt).where(Prompt.node_name == node, Prompt.is_active))
    if prompt is None:
        raise PromptError(f"No active prompt for {node}")
    return prompt


def list_versions(db: Session, node: str) -> list[Prompt]:
    _contract(node)
    return list(
        db.scalars(select(Prompt).where(Prompt.node_name == node).order_by(Prompt.version.desc()))
    )


def active_versions(db: Session) -> dict[str, Prompt]:
    return {p.node_name: p for p in db.scalars(select(Prompt).where(Prompt.is_active))}


def _set_active(db: Session, node: str, target: Prompt) -> Prompt | None:
    current = db.scalar(select(Prompt).where(Prompt.node_name == node, Prompt.is_active))
    if current is not None and current.id != target.id:
        current.is_active = False
        db.flush()  # clear the one-active-per-node index before activating the target
    target.is_active = True
    return current


def _commit(db: Session, node: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise PromptError(f"Another change to {node} happened at the same time. Reload and retry")


def create_version(
    db: Session,
    node: str,
    template: str,
    model: str,
    temperature: float,
    user: User,
    note: str | None = None,
    activate: bool = False,
) -> Prompt:
    contract = _contract(node)
    validate(node, template)
    _check_settings(model, temperature)

    latest = db.scalar(select(func.max(Prompt.version)).where(Prompt.node_name == node))
    prompt = Prompt(
        node_name=node,
        version=(latest or 0) + 1,
        template=template,
        required_variables=list(contract.required),
        model=model.strip(),
        temperature=temperature,
        is_active=False,
        note=note,
        created_by=user.id,
    )
    db.add(prompt)
    db.flush()
    audit.record(db, user, "prompts.save", "prompt", prompt.id, {"node": node, "version": prompt.version})
    if activate:
        previous = _set_active(db, node, prompt)
        audit.record(
            db, user, "prompts.activate", "prompt", prompt.id,
            {"node": node, "version": prompt.version, "previous_version": previous.version if previous else None},
        )
    _commit(db, node)
    return prompt


def activate(db: Session, node: str, version: int, user: User) -> Prompt:
    _contract(node)
    target = db.scalar(select(Prompt).where(Prompt.node_name == node, Prompt.version == version))
    if target is None:
        raise PromptError(f"{node} version {version} not found")
    if target.is_active:
        raise PromptError(f"{node} version {version} is already active")
    # An old version may predate a variable the node now requires.
    validate(node, target.template)
    previous = _set_active(db, node, target)
    audit.record(
        db, user, "prompts.activate", "prompt", target.id,
        {"node": node, "version": version, "previous_version": previous.version if previous else None},
    )
    _commit(db, node)
    return target


def reset_to_default(db: Session, node: str, user: User) -> Prompt:
    contract = _contract(node)
    return create_version(
        db, node, contract.template, contract.model, contract.temperature, user,
        note="Reset to default", activate=True,
    )


def diff(db: Session, node: str, version_a: int, version_b: int) -> str:
    rows = {
        p.version: p
        for p in db.scalars(
            select(Prompt).where(Prompt.node_name == node, Prompt.version.in_([version_a, version_b]))
        )
    }
    for v in (version_a, version_b):
        if v not in rows:
            raise PromptError(f"{node} version {v} not found")
    a, b = rows[version_a], rows[version_b]
    text_a = f"model: {a.model}\ntemperature: {a.temperature}\n\n{a.template}"
    text_b = f"model: {b.model}\ntemperature: {b.temperature}\n\n{b.template}"
    return "".join(
        difflib.unified_diff(
            text_a.splitlines(keepends=True),
            text_b.splitlines(keepends=True),
            fromfile=f"{node} v{version_a}",
            tofile=f"{node} v{version_b}",
        )
    )


def test_on_sample(
    db: Session,
    node: str,
    template: str,
    model: str,
    temperature: float,
    variables: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Render with sample data (overridable) and run it. Writes nothing. Returns (prompt, output)."""
    contract = _contract(node)
    validate(node, template)
    _check_settings(model, temperature)
    unknown = sorted(set(variables or {}) - set(contract.variables))
    if unknown:
        raise PromptError(f"Unknown variable(s): {', '.join(unknown)}")
    rendered = render(node, template, {**contract.sample, **(variables or {})})
    try:
        output = llm.complete(db, model.strip(), rendered, temperature)
    except llm.LLMError as exc:
        raise PromptError(str(exc))
    return rendered, output


def seed_defaults(db: Session) -> list[str]:
    """Create and activate version 1 for nodes that have no prompt rows yet."""
    seeded = []
    existing = set(db.scalars(select(Prompt.node_name).distinct()))
    for node, contract in NODES.items():
        if node in existing:
            continue
        validate(node, contract.template)  # a broken default should fail loudly at startup
        db.add(
            Prompt(
                node_name=node,
                version=1,
                template=contract.template,
                required_variables=list(contract.required),
                model=contract.model,
                temperature=contract.temperature,
                is_active=True,
                note=SYSTEM_DEFAULT_NOTE,
            )
        )
        audit.record(db, None, "prompts.seed", "node", node)
        seeded.append(node)
    db.commit()
    return seeded


SYSTEM_DEFAULT_NOTE = "Default"


def upgrade_system_defaults(db: Session) -> list[str]:
    """When the code's default prompt improves, move nodes still on an older *system* default to it.

    Only touches an active version the system created (no author, note "Default"). Anything a person
    saved, activated or reset keeps running untouched. The old version stays available for rollback.
    """
    upgraded = []
    for node, contract in NODES.items():
        active = db.scalar(select(Prompt).where(Prompt.node_name == node, Prompt.is_active))
        if active is None or active.created_by is not None or active.note != SYSTEM_DEFAULT_NOTE:
            continue
        # A person rolling back to an old default leaves it authorless; the audit log remembers.
        chosen_by_person = db.scalar(
            select(AuditLog.id).where(AuditLog.action == "prompts.activate", AuditLog.target_id == str(active.id)).limit(1)
        )
        if chosen_by_person:
            continue
        if (active.template, active.model, active.temperature) == (contract.template, contract.model, contract.temperature):
            continue
        validate(node, contract.template)
        latest = db.scalar(select(func.max(Prompt.version)).where(Prompt.node_name == node))
        active.is_active = False
        db.flush()
        new = Prompt(
            node_name=node, version=latest + 1, template=contract.template,
            required_variables=list(contract.required), model=contract.model,
            temperature=contract.temperature, is_active=True, note=SYSTEM_DEFAULT_NOTE,
        )
        db.add(new)
        db.flush()
        audit.record(db, None, "prompts.upgrade_default", "prompt", new.id,
                     {"node": node, "from_version": active.version, "to_version": new.version})
        upgraded.append(node)
    db.commit()
    return upgraded

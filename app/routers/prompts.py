from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import prompts
from app.auth import require
from app.db import get_db
from app.models import Prompt, User
from app.prompt_defaults import NODES
from app.schemas import DiffOut, NodePromptOut, PromptCreate, PromptOut, PromptTestIn, PromptTestOut

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("", response_model=list[NodePromptOut])
def list_nodes(
    db: Session = Depends(get_db), _: User = Depends(require("prompts.view"))
) -> list[NodePromptOut]:
    active = prompts.active_versions(db)
    return [
        NodePromptOut(
            node=node,
            description=contract.description,
            required_variables=list(contract.required),
            optional_variables=list(contract.optional),
            active=PromptOut.model_validate(active[node]) if node in active else None,
        )
        for node, contract in NODES.items()
    ]


@router.get("/{node}/versions", response_model=list[PromptOut])
def list_versions(
    node: str, db: Session = Depends(get_db), _: User = Depends(require("prompts.view"))
) -> list[Prompt]:
    return prompts.list_versions(db, node)


@router.get("/{node}/diff", response_model=DiffOut)
def diff(
    node: str, a: int, b: int,
    db: Session = Depends(get_db), _: User = Depends(require("prompts.view")),
) -> DiffOut:
    return DiffOut(diff=prompts.diff(db, node, a, b))


@router.post("/{node}/versions", response_model=PromptOut, status_code=status.HTTP_201_CREATED)
def create_version(
    node: str,
    body: PromptCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require("prompts.edit")),
) -> Prompt:
    return prompts.create_version(
        db, node, body.template, body.model, body.temperature, user,
        note=body.note, activate=body.activate,
    )


@router.post("/{node}/activate/{version}", response_model=PromptOut)
def activate(
    node: str, version: int,
    db: Session = Depends(get_db), user: User = Depends(require("prompts.edit")),
) -> Prompt:
    return prompts.activate(db, node, version, user)


@router.post("/{node}/reset", response_model=PromptOut)
def reset(
    node: str, db: Session = Depends(get_db), user: User = Depends(require("prompts.edit"))
) -> Prompt:
    return prompts.reset_to_default(db, node, user)


@router.post("/{node}/test", response_model=PromptTestOut)
def test(
    node: str,
    body: PromptTestIn,
    db: Session = Depends(get_db),
    _: User = Depends(require("prompts.edit")),
) -> PromptTestOut:
    rendered, output = prompts.test_on_sample(
        db, node, body.template, body.model, body.temperature, body.variables
    )
    return PromptTestOut(rendered_prompt=rendered, output=output)

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import UserRole


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthOut(BaseModel):
    status: str
    db: str


class ResumeOut(ORM):
    id: uuid.UUID
    filename: str
    status: str
    uploaded_at: datetime


# --- users / auth ----------------------------------------------------------

class UserOut(ORM):
    id: uuid.UUID
    email: str
    name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class MeOut(UserOut):
    permissions: list[str]


class UserCreate(BaseModel):
    email: str
    name: str | None = None
    role: UserRole = UserRole.viewer

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        local, _, domain = v.partition("@")
        if not local or "." not in domain:
            raise ValueError("Not a valid email address")
        return v


class UserUpdate(BaseModel):
    name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class AuditOut(ORM):
    id: uuid.UUID
    user_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict | None
    created_at: datetime


# --- vault -------------------------------------------------------------------

class ProviderStatusOut(ORM):
    provider: str
    configured: bool
    fields: list[str]
    version: int | None
    last4: str | None
    created_at: datetime | None
    created_by: str | None
    last_tested_at: datetime | None
    last_test_ok: bool | None


class CredentialVersionOut(ORM):
    version: int
    status: str
    last4: str
    created_at: datetime
    retired_at: datetime | None
    last_tested_at: datetime | None
    last_test_ok: bool | None


class CredentialIn(BaseModel):
    values: dict[str, str]


class TestResultOut(BaseModel):
    ok: bool
    message: str


# --- prompts -----------------------------------------------------------------

class PromptOut(ORM):
    id: uuid.UUID
    node_name: str
    version: int
    template: str
    required_variables: list[str]
    model: str
    temperature: float
    is_active: bool
    note: str | None
    created_by: uuid.UUID | None
    created_at: datetime


class NodePromptOut(BaseModel):
    node: str
    description: str
    required_variables: list[str]
    optional_variables: list[str]
    active: PromptOut | None


class PromptCreate(BaseModel):
    template: str
    model: str
    temperature: float = Field(ge=0, le=2)
    note: str | None = None
    activate: bool = False


class PromptTestIn(BaseModel):
    template: str
    model: str
    temperature: float = Field(ge=0, le=2)
    variables: dict[str, str] | None = None


class PromptTestOut(BaseModel):
    rendered_prompt: str
    output: str


class DiffOut(BaseModel):
    diff: str


# --- discovery runs ----------------------------------------------------------

class RunCreate(BaseModel):
    resume_id: uuid.UUID
    num_startups: int = Field(5, ge=1, le=20)
    num_kdms_per_company: int = Field(5, ge=1, le=10)


class RunOut(ORM):
    id: uuid.UUID
    resume_id: uuid.UUID
    num_startups: int
    num_kdms_per_company: int
    status: str
    error: str | None
    usage: dict | None
    langfuse_trace_url: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class StartupOut(ORM):
    id: uuid.UUID
    name: str
    domain: str | None
    website: str | None
    description: str | None
    source_url: str | None
    relevance: float | None


class CandidateOut(ORM):
    id: uuid.UUID
    run_id: uuid.UUID
    startup_id: uuid.UUID
    startup_name: str
    name: str
    title: str | None
    linkedin_url: str
    reason: str | None
    existing_contact_id: uuid.UUID | None
    status: str
    cv_filename: str


class RunDetailOut(RunOut):
    resume_filename: str
    startups: list[StartupOut]
    candidates: list[CandidateOut]


class RunEventOut(ORM):
    id: int
    node: str
    kind: str
    message: str
    data: dict | None
    created_at: datetime

"""Role → permission map (PLAN.md §9). The backend check is the real gate; the UI only mirrors it."""

from app.models import UserRole

VIEWER = frozenset({
    "dashboard.view",  # stats, library, candidates, contacts, replies
})

OPERATOR = VIEWER | {
    "resumes.upload",
    "runs.start",
    "contacts.act",  # approve/reuse/update candidates, run Apollo, manual email
    "drafts.act",  # generate, edit, approve drafts
    "emails.send",
    "prompts.view",
}

ADMIN = OPERATOR | {
    "resumes.delete",
    "mode.toggle",
    "vault.manage",
    "prompts.edit",
    "users.manage",
    "audit.view",
}

PERMISSIONS: dict[UserRole, frozenset[str]] = {
    UserRole.viewer: frozenset(VIEWER),
    UserRole.operator: frozenset(OPERATOR),
    UserRole.admin: frozenset(ADMIN),
}


def has_permission(role: UserRole, permission: str) -> bool:
    return permission in PERMISSIONS[role]

"""The permission vocabulary.

Closed on purpose: a permission that no code path checks is a promise the product does not
keep, so codes live here beside the checks rather than being typed into a form.

Each is the verb an `access.py` helper asks about. Adding one means adding the check that
reads it, in the same change.
"""

from __future__ import annotations

from typing import NamedTuple


class PermissionSpec(NamedTuple):
    code: str
    description: str


PROJECT_CREATE = "project.create"
PROJECT_MANAGE_ANY = "project.manage_any"
PROJECT_VIEW_ANY = "project.view_any"
USER_MANAGE = "user.manage"
APPROVAL_DECIDE_ANY = "approval.decide_any"
TIMESHEET_LOG = "timesheet.log"

ALL: tuple[PermissionSpec, ...] = (
    PermissionSpec(PROJECT_CREATE, "Create projects"),
    PermissionSpec(PROJECT_MANAGE_ANY, "Edit any project, its members and its tasks"),
    PermissionSpec(PROJECT_VIEW_ANY, "Read every project, whether a member or not"),
    PermissionSpec(USER_MANAGE, "Add and manage people"),
    PermissionSpec(APPROVAL_DECIDE_ANY, "Decide any entry, not only your own queue"),
    PermissionSpec(TIMESHEET_LOG, "Log time on projects you belong to"),
)

CODES: tuple[str, ...] = tuple(spec.code for spec in ALL)

# What each seeded role starts with. The administrator holds everything, so
# authorization needs no special case for it — the lookup simply always succeeds.
ADMINISTRATOR_GRANTS: tuple[str, ...] = CODES
MEMBER_GRANTS: tuple[str, ...] = (TIMESHEET_LOG,)

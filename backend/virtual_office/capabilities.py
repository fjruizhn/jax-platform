from __future__ import annotations

from enum import StrEnum


class OfficeOperation(StrEnum):
    VIEW_PROJECT = "VIEW_PROJECT"
    MANAGE_PROJECT = "MANAGE_PROJECT"
    PROFESSIONAL_ACTION = "PROFESSIONAL_ACTION"
    ADMINISTER_OFFICE = "ADMINISTER_OFFICE"
    ADMINISTER_OFFICE_IDENTITY_SECURITY = "ADMINISTER_OFFICE_IDENTITY_SECURITY"
    CREATE_OFFICE_PROJECT = "CREATE_OFFICE_PROJECT"
    ADMINISTER_PROJECT_MEMBERS = "ADMINISTER_PROJECT_MEMBERS"
    REQUEST_MODULE_PUBLICATION = "REQUEST_MODULE_PUBLICATION"


PROJECT_OPERATIONS = frozenset({OfficeOperation.VIEW_PROJECT, OfficeOperation.MANAGE_PROJECT, OfficeOperation.PROFESSIONAL_ACTION,
                                OfficeOperation.ADMINISTER_PROJECT_MEMBERS, OfficeOperation.REQUEST_MODULE_PUBLICATION})
ADMIN_OPERATIONS = frozenset({OfficeOperation.ADMINISTER_OFFICE, OfficeOperation.ADMINISTER_OFFICE_IDENTITY_SECURITY})


class MFARequirement(StrEnum):
    NONE = "NONE"
    CONTEXTUAL = "CONTEXTUAL"
    RECENT = "RECENT"


# This is deliberately exhaustive.  A new Office operation must state its
# MFA policy instead of inheriting a broad "everything except view" rule.
MFA_REQUIREMENT_BY_OPERATION = {
    OfficeOperation.VIEW_PROJECT: MFARequirement.CONTEXTUAL,
    OfficeOperation.REQUEST_MODULE_PUBLICATION: MFARequirement.NONE,
    OfficeOperation.MANAGE_PROJECT: MFARequirement.RECENT,
    OfficeOperation.PROFESSIONAL_ACTION: MFARequirement.RECENT,
    OfficeOperation.ADMINISTER_OFFICE: MFARequirement.RECENT,
    OfficeOperation.ADMINISTER_OFFICE_IDENTITY_SECURITY: MFARequirement.RECENT,
    OfficeOperation.CREATE_OFFICE_PROJECT: MFARequirement.RECENT,
    OfficeOperation.ADMINISTER_PROJECT_MEMBERS: MFARequirement.RECENT,
}
if frozenset(MFA_REQUIREMENT_BY_OPERATION) != frozenset(OfficeOperation):
    raise RuntimeError("Office MFA policy must cover every operation exactly once")


def operation_requires_project(operation: OfficeOperation) -> bool:
    return operation in PROJECT_OPERATIONS


def grant_permits(operation: OfficeOperation, grant_role: str, audience: str) -> bool:
    if operation is OfficeOperation.VIEW_PROJECT:
        return grant_role in {"COLLABORATOR", "PROJECT_ADMIN"} and audience in {"INTERNAL", "CLIENT_PUBLISHED"}
    return operation in {OfficeOperation.MANAGE_PROJECT, OfficeOperation.REQUEST_MODULE_PUBLICATION} and grant_role == "PROJECT_ADMIN" and audience == "INTERNAL"

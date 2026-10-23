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


PROJECT_OPERATIONS = frozenset({OfficeOperation.VIEW_PROJECT, OfficeOperation.MANAGE_PROJECT, OfficeOperation.PROFESSIONAL_ACTION,
                                OfficeOperation.ADMINISTER_PROJECT_MEMBERS})
ADMIN_OPERATIONS = frozenset({OfficeOperation.ADMINISTER_OFFICE, OfficeOperation.ADMINISTER_OFFICE_IDENTITY_SECURITY})


def operation_requires_project(operation: OfficeOperation) -> bool:
    return operation in PROJECT_OPERATIONS


def grant_permits(operation: OfficeOperation, grant_role: str, audience: str) -> bool:
    if operation is OfficeOperation.VIEW_PROJECT:
        return grant_role in {"COLLABORATOR", "PROJECT_ADMIN"} and audience in {"INTERNAL", "CLIENT_PUBLISHED"}
    return operation is OfficeOperation.MANAGE_PROJECT and grant_role == "PROJECT_ADMIN" and audience == "INTERNAL"

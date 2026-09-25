"""Authentication, storage-state validation, and CI secret injection for AIQA."""

from aiqa.auth.workflow import (
    RoleAccount,
    StorageStateValidationResult,
    inject_ci_secrets,
    resolve_role_storage_state,
    resolve_test_case_secrets,
    validate_storage_state,
)

__all__ = [
    "RoleAccount",
    "StorageStateValidationResult",
    "inject_ci_secrets",
    "resolve_role_storage_state",
    "resolve_test_case_secrets",
    "validate_storage_state",
]

"""Domain errors. API layer maps them to HTTP status codes."""

from __future__ import annotations


class FleetPilotError(Exception):
    status_code = 400
    code = "error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(FleetPilotError):
    status_code = 404
    code = "not_found"


class TenantAccessError(FleetPilotError):
    """Raised when a request touches a resource outside the caller's tenant.

    Deliberately reported as 404 by the API so we never confirm another tenant's IDs exist.
    """

    status_code = 404
    code = "not_found"


class PermissionDeniedError(FleetPilotError):
    status_code = 403
    code = "permission_denied"


class ValidationFailedError(FleetPilotError):
    status_code = 422
    code = "validation_failed"


class InsufficientEvidenceError(FleetPilotError):
    status_code = 422
    code = "insufficient_evidence"


class ConflictError(FleetPilotError):
    status_code = 409
    code = "conflict"


class ToolExecutionError(FleetPilotError):
    status_code = 500
    code = "tool_error"


class LLMProviderError(FleetPilotError):
    status_code = 502
    code = "llm_unavailable"

"""Authenticated tenant context. Built by the API from the bearer token — never from user input or
LLM output. Every repository, tool and service takes this object and scopes by `company_id`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["viewer", "operator", "admin"]

PERMISSIONS: dict[str, set[str]] = {
    "viewer": {"telemetry:read"},
    "operator": {"telemetry:read", "actions:propose"},
    "admin": {"telemetry:read", "actions:propose", "actions:approve", "settings:write"},
}


@dataclass(frozen=True)
class TenantContext:
    company_id: str
    user_id: str
    role: Role = "viewer"
    display_name: str | None = None

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS.get(self.role, set())

"""Action integration adapters.

No real ticketing/procurement/messaging credentials exist for this assessment, so the only adapter
is `SimulatedAdapter`. It is explicit about being a simulation: every result carries
`"simulated": True` and references are prefixed `SIM-`. A real integration (ServiceNow, Jira,
procurement API, email) would implement the same `ActionAdapter` protocol.
"""

from __future__ import annotations

import hashlib
from typing import Protocol


class ActionAdapter(Protocol):
    name: str

    def execute(self, action_type: str, params: dict, *, proposal_id: str, company_id: str) -> dict: ...


class SimulatedAdapter:
    name = "simulated"
    PREFIX = {"create_upgrade_order": "SIM-PO", "open_remediation_ticket": "SIM-TKT",
              "flag_device_for_replacement": "SIM-RPL", "notify_employee": "SIM-MSG"}

    def execute(self, action_type: str, params: dict, *, proposal_id: str, company_id: str) -> dict:
        # Deterministic reference: the same proposal always maps to the same simulated record.
        ref = f"{self.PREFIX.get(action_type, 'SIM')}-{hashlib.sha1(proposal_id.encode()).hexdigest()[:8].upper()}"
        return {"simulated": True, "adapter": self.name, "external_ref": ref, "status": "recorded",
                "message": f"Simulated {action_type.replace('_', ' ')} recorded (no external system was called)."}


class FailingAdapter:
    """Used by tests/evals to exercise execution-failure handling."""

    name = "failing"

    def execute(self, action_type: str, params: dict, *, proposal_id: str, company_id: str) -> dict:
        raise RuntimeError("downstream system unavailable")


_adapter: ActionAdapter = SimulatedAdapter()


def get_adapter() -> ActionAdapter:
    return _adapter


def set_adapter(adapter: ActionAdapter) -> None:
    global _adapter
    _adapter = adapter

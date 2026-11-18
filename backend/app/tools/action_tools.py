"""State-changing tools. They never execute anything: each one validates its arguments, has the
server gather supporting evidence, and persists a proposal in `pending_approval`. Execution only
happens after an authorized human approves the exact proposal in the Approval Center."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from app.services.actions import ActionService
from app.tools.base import ToolContext, ToolInput, ToolOutput, tool
from app.tools.read_tools import DEVICE_ID_PATTERN

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(v: str) -> str:
    return _CTRL.sub("", v).strip()


CleanStr = Annotated[str, AfterValidator(_clean)]


class UpgradeArgs(ToolInput):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    component: Literal["memory", "storage", "battery"]
    spec: CleanStr = Field(min_length=3, max_length=200, description="Requested specification, e.g. '1 TB SSD'")


class TicketArgs(ToolInput):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    check_id: str = Field(pattern=r"^[a-z0-9_]{2,64}$")
    note: CleanStr = Field(min_length=3, max_length=1000)


class ReplacementArgs(ToolInput):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    reason: CleanStr = Field(min_length=3, max_length=500)


class NotifyArgs(ToolInput):
    employee_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{2,64}$")
    message: CleanStr = Field(min_length=3, max_length=1000)


def _propose(ctx: ToolContext, action_type: str, params: dict) -> ToolOutput:
    svc = ActionService(ctx.session, ctx.tenant, ctx.settings)
    p, created = svc.propose(action_type, params, agent_run_id=ctx.agent_run_id, analysis=ctx.analysis)
    from app.schemas.evidence import Evidence

    evidence = [Evidence.model_validate(e) for e in p.evidence]
    verb = "Created" if created else "Re-used existing open"
    return ToolOutput(
        summary=f"{verb} proposal {p.id[:8]} ({action_type}) — status {p.status}; requires human approval. "
                f"Justification: {p.justification}",
        data={"proposal_id": p.id, "action_type": action_type, "params": params, "status": p.status,
              "created": created, "payload_hash": p.payload_hash, "justification": p.justification,
              "expires_at": p.expires_at.isoformat(), "evidence_ids": [e.evidence_id for e in evidence]},
        evidence=evidence,
    )


@tool("create_upgrade_order", "Propose a hardware upgrade order (memory, storage or battery) for a device. "
      "Creates a pending_approval proposal; refused without supporting telemetry.", UpgradeArgs,
      kind="action", permission="actions:propose")
def create_upgrade_order(ctx: ToolContext, a: UpgradeArgs) -> ToolOutput:
    return _propose(ctx, "create_upgrade_order", a.model_dump())


@tool("open_remediation_ticket", "Propose a remediation ticket for a compliance check that is failing on a "
      "device in its latest snapshot. Creates a pending_approval proposal.", TicketArgs,
      kind="action", permission="actions:propose")
def open_remediation_ticket(ctx: ToolContext, a: TicketArgs) -> ToolOutput:
    return _propose(ctx, "open_remediation_ticket", a.model_dump())


@tool("flag_device_for_replacement", "Propose flagging a device for replacement (requires at_risk or critical "
      "risk). Creates a pending_approval proposal.", ReplacementArgs, kind="action", permission="actions:propose")
def flag_device_for_replacement(ctx: ToolContext, a: ReplacementArgs) -> ToolOutput:
    return _propose(ctx, "flag_device_for_replacement", a.model_dump())


@tool("notify_employee", "Propose a notification to an employee about findings on their devices. Creates a "
      "pending_approval proposal.", NotifyArgs, kind="action", permission="actions:propose")
def notify_employee(ctx: ToolContext, a: NotifyArgs) -> ToolOutput:
    return _propose(ctx, "notify_employee", a.model_dump())

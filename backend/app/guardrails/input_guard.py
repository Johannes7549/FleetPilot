"""First node of the agent: screens the raw user request before any planning happens."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.guardrails.injection import is_suspicious

MAX_QUESTION_CHARS = 2000

APPROVAL_BYPASS = re.compile(
    r"(\b(approve|execute|carry out)\b.{0,40}\b(action|proposal|order|ticket|request|it|them|all)\b"
    r"|\bskip\b.{0,20}\bapproval\b|\bwithout\b.{0,20}\bapproval\b|\bbypass\b|\bauto-?approve\b|"
    r"\bmark\b.{0,30}\bas approved\b)",
    re.IGNORECASE,
)
# "approve" used as a question about the approval process is fine ("what needs approval?").
APPROVAL_QUESTION = re.compile(r"^\s*(what|which|how many|show|list)\b.*\b(pending|awaiting|need|needs)\b", re.IGNORECASE)


@dataclass
class GuardResult:
    blocked: bool = False
    reason: str | None = None
    code: str | None = None
    flags: list[str] = field(default_factory=list)


def screen_question(question: str, *, own_company_id: str, own_company_name: str,
                    other_companies: dict[str, str]) -> GuardResult:
    """`other_companies` maps company_id -> display name for tenants other than the caller's.
    Only identifiers/names are used, to detect explicit cross-tenant requests; no data is read."""
    q = question.strip()
    if not q:
        return GuardResult(True, "The request is empty.", "empty")
    if len(q) > MAX_QUESTION_CHARS:
        return GuardResult(True, f"The request exceeds {MAX_QUESTION_CHARS} characters.", "too_long")

    lowered = q.lower()
    for cid, name in other_companies.items():
        if re.search(rf"\b{re.escape(cid.lower())}\b", lowered) or re.search(rf"\b{re.escape(name.lower())}\b", lowered):
            return GuardResult(True, f"You are signed in to {own_company_name}. Data belonging to other "
                                     f"organisations cannot be accessed.", "cross_tenant", ["cross_tenant_reference"])

    if APPROVAL_BYPASS.search(q) and not APPROVAL_QUESTION.search(q):
        return GuardResult(True, "Approving or executing actions is not possible from the copilot. Proposed actions "
                                 "must be approved by an authorised administrator in the Approval Center.",
                           "approval_bypass", ["approval_bypass_attempt"])

    if is_suspicious(q):
        return GuardResult(True, "The request contains instructions that try to change the assistant's rules; "
                                 "it was not processed.", "prompt_injection", ["prompt_injection"])
    return GuardResult()

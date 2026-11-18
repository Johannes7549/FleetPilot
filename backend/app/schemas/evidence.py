"""Structured evidence. Evidence is produced by deterministic tools from database rows — never by
the LLM. Responses reference evidence by `evidence_id`; the validator re-checks each reference
against the database before a response or action is allowed through."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    evidence_id: str
    device_id: str | None = None
    company_id: str
    snapshot_id: str | None = None
    collected_at: datetime | None = None
    metric: str
    observed_value: Any = None
    threshold: float | str | None = None
    comparator: Literal["<", "<=", ">", ">=", "==", "!=", "in", "info"] = "info"
    unit: str | None = None
    source: str = "device_snapshots"
    temporal_scope: Literal["latest", "historical", "window_aggregate"] = "latest"
    note: str | None = None

    @staticmethod
    def make_id(*parts: object) -> str:
        return "ev_" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:14]


def ev(
    *,
    company_id: str,
    metric: str,
    observed_value: Any,
    device_id: str | None = None,
    snapshot_id: str | None = None,
    collected_at: datetime | None = None,
    threshold: float | str | None = None,
    comparator: str = "info",
    unit: str | None = None,
    source: str = "device_snapshots",
    temporal_scope: str = "latest",
    note: str | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=Evidence.make_id(company_id, device_id, snapshot_id, metric, temporal_scope, observed_value),
        device_id=device_id,
        company_id=company_id,
        snapshot_id=snapshot_id,
        collected_at=collected_at,
        metric=metric,
        observed_value=observed_value,
        threshold=threshold,
        comparator=comparator,  # type: ignore[arg-type]
        unit=unit,
        source=source,
        temporal_scope=temporal_scope,  # type: ignore[arg-type]
        note=note,
    )


class EvidenceBundle(BaseModel):
    items: list[Evidence] = Field(default_factory=list)

    def add(self, *items: Evidence) -> None:
        seen = {e.evidence_id for e in self.items}
        for it in items:
            if it.evidence_id not in seen:
                self.items.append(it)
                seen.add(it.evidence_id)

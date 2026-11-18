from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any


def event(step_type: str, name: str, *, status: str = "ok", input: Any = None, output: Any = None,
          evidence_ids: list[str] | None = None, started: float | None = None, duration_ms: int | None = None,
          **extra: Any) -> dict:
    return {
        "step_type": step_type,
        "name": name,
        "status": status,
        "input": input,
        "output": output,
        "evidence_ids": evidence_ids or [],
        "duration_ms": duration_ms if duration_ms is not None else (int((time.perf_counter() - started) * 1000) if started else 0),
        "started_at": datetime.now(UTC).isoformat(),
        **extra,
    }

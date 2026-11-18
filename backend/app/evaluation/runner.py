"""Runs every evaluation case and writes JSON + Markdown reports."""

from __future__ import annotations

import json
import time
import traceback
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from app.evaluation.cases import CASES, Env
from app.evaluation.harness import build_database

CATEGORY_ORDER = ["factual", "grounding", "temporal", "safety", "tool_selection", "failure"]


def run(out_dir: Path, *, db_url: str | None = None, llm=None, only: str | None = None) -> dict:
    t0 = time.perf_counter()
    shared = build_database(db_url)
    env = Env(shared=shared, fresh=lambda: build_database(None), llm=llm)
    results = []
    for c in CASES:
        if only and not (c.id.startswith(only) or c.category == only):
            continue
        t = time.perf_counter()
        try:
            passed, details = c.fn(env)
        except Exception as exc:  # noqa: BLE001 - a crashing case is a failed case
            passed, details = False, f"EXCEPTION {exc.__class__.__name__}: {exc}\n{traceback.format_exc(limit=3)}"
        results.append({"id": c.id, "category": c.category, "description": c.description, "passed": bool(passed),
                        "details": str(details)[:600], "duration_ms": int((time.perf_counter() - t) * 1000)})
        print(f"  [{'PASS' if passed else 'FAIL'}] {c.id:5} {c.category:14} {c.description}")

    by_cat: dict[str, dict] = defaultdict(lambda: {"total": 0, "passed": 0})
    for r in results:
        by_cat[r["category"]]["total"] += 1
        by_cat[r["category"]]["passed"] += r["passed"]
    for v in by_cat.values():
        v["accuracy"] = round(v["passed"] / v["total"] * 100, 1)
    total, passed = len(results), sum(r["passed"] for r in results)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "llm:" + getattr(llm, "name", "") if llm else "deterministic",
        "database": "postgresql" if db_url and db_url.startswith("postgres") else "sqlite (in-memory)",
        "total": total, "passed": passed, "failed": total - passed,
        "accuracy": round(passed / total * 100, 1) if total else 0.0,
        "by_category": {k: by_cat[k] for k in CATEGORY_ORDER if k in by_cat},
        "duration_s": round(time.perf_counter() - t0, 1),
        "results": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "latest.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(r: dict) -> str:
    lines = ["# FleetPilot evaluation report", "",
             f"Generated {r['generated_at']} · mode **{r['mode']}** · database {r['database']} · {r['duration_s']}s", "",
             f"**{r['passed']}/{r['total']} passed ({r['accuracy']}%)**", "",
             "| Category | Passed | Total | Accuracy |", "|---|---|---|---|"]
    for k, v in r["by_category"].items():
        lines.append(f"| {k} | {v['passed']} | {v['total']} | {v['accuracy']}% |")
    failed = [x for x in r["results"] if not x["passed"]]
    lines += ["", "## Failures", ""]
    lines += [f"- **{x['id']}** ({x['category']}) {x['description']}\n  ```\n  {x['details']}\n  ```" for x in failed] or ["None."]
    lines += ["", "## All cases", "", "| ID | Category | Result | Description | Details |", "|---|---|---|---|---|"]
    for x in r["results"]:
        det = x["details"].replace("|", "\\|").replace("\n", " ")[:140]
        lines.append(f"| {x['id']} | {x['category']} | {'✅' if x['passed'] else '❌'} | {x['description']} | {det} |")
    return "\n".join(lines) + "\n"

"""Run the complete FleetPilot evaluation suite.

    python scripts/run_evaluations.py                 # deterministic agent, in-memory SQLite (no services, no API cost)
    python scripts/run_evaluations.py --llm           # use the configured LLM provider for planning/answers
    python scripts/run_evaluations.py --db postgresql+psycopg://...   # run against PostgreSQL (dedicated DB!)
    python scripts/run_evaluations.py --only safety   # one category or case-id prefix

Reports: evaluation/results/latest.md and latest.json. Exit code 1 if any case fails.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="use FLEETPILOT_LLM_PROVIDER instead of deterministic mode")
    ap.add_argument("--db", default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--out", default=str(ROOT / "evaluation" / "results"))
    args = ap.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    from app.agents.llm.providers import get_provider
    from app.evaluation.runner import run

    llm = None
    if args.llm:
        llm = get_provider()
        if llm is None:
            print("No LLM provider configured (set FLEETPILOT_LLM_PROVIDER and its API key).")
            return 2
    print(f"FleetPilot evaluation — mode: {'llm:' + llm.name if llm else 'deterministic'}")
    report = run(Path(args.out), db_url=args.db, llm=llm, only=args.only)
    print(f"\n{report['passed']}/{report['total']} passed ({report['accuracy']}%) in {report['duration_s']}s")
    for k, v in report["by_category"].items():
        print(f"  {k:15} {v['passed']}/{v['total']}  ({v['accuracy']}%)")
    print(f"\nReport: {Path(args.out) / 'latest.md'}")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

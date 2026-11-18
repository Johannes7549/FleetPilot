"""Run the FleetPilot LLM evaluation suite (separate from the 65-case deterministic suite).

    python scripts/run_llm_evaluations.py            # mocked provider (no key, no cost); live marked NOT EXECUTED
    python scripts/run_llm_evaluations.py --live     # also run against FLEETPILOT_LLM_PROVIDER (needs its API key)
    python scripts/run_llm_evaluations.py --only multi_step

Reports: evaluation/results/llm_latest.md and llm_latest.json. Exit code 1 if an executed suite has failures.
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
    ap.add_argument("--live", action="store_true", help="also evaluate the configured real LLM provider")
    ap.add_argument("--no-mock", action="store_true", help="skip the mocked-provider run")
    ap.add_argument("--only", default=None, help="category or case-id prefix")
    ap.add_argument("--out", default=str(ROOT / "evaluation" / "results"))
    args = ap.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    from app.agents.llm.providers import get_provider, llm_status
    from app.evaluation.llm_runner import run_suite, write_report

    mocked = {"executed": False, "reason": "skipped (--no-mock)"}
    if not args.no_mock:
        print("FleetPilot LLM evaluation — MOCKED provider (scripted HTTP transport; not a real model)")
        mocked = run_suite("mocked", only=args.only)
        print(f"  mocked: {mocked['passed']}/{mocked['total']} passed\n")

    status = llm_status()
    if not args.live:
        live = {"executed": False, "reason": "not requested (run with --live and a configured provider + API key)"}
    elif status["active_mode"] not in ("llm", "hybrid"):
        live = {"executed": False, "reason": f"no usable provider: configured '{status['configured_provider']}'"
                + (f" — {status['unavailable_reason']}" if status["unavailable_reason"] else "")}
    else:
        print(f"FleetPilot LLM evaluation — LIVE provider {status['provider']} / {status['model']}")
        live = run_suite("live", provider_factory=get_provider, only=args.only, baseline=False)
        live.update(provider=status["provider"], model=status["model"])
        print(f"  live: {live['passed']}/{live['total']} passed\n")
    if not live["executed"]:
        print(f"Live provider evaluation NOT EXECUTED: {live['reason']}")

    write_report(Path(args.out), mocked, live)
    print(f"Report: {Path(args.out) / 'llm_latest.md'}")
    failed = any(r.get("executed") and r["passed"] < r["total"] for r in (mocked, live))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

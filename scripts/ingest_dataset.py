"""Run migrations, ingest the NDJSON dataset (idempotent) and seed demo users.

Usage:
    python scripts/ingest_dataset.py [--file data/device-telemetry-dataset.ndjson] [--skip-migrations]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.ingestion.loader import ingest_file  # noqa: E402
from app.ingestion.seed import seed_users  # noqa: E402


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    command.upgrade(cfg, "head")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=get_settings().dataset_path)
    ap.add_argument("--skip-migrations", action="store_true")
    args = ap.parse_args()
    if not args.skip_migrations:
        migrate()
    with session_scope() as s:
        stats = ingest_file(s, args.file)
        users = seed_users(s)
    report = stats.as_dict()
    print(json.dumps({k: v for k, v in report.items() if k != "errors"}, indent=2))
    if report["errors"]:
        print(f"\n{len(report['errors'])} problem record(s):")
        for e in report["errors"][:20]:
            print(f"  line {e['line']}: [{e['kind']}] {e['reason']}")
    print(f"\nSeeded {len(users)} new demo user(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

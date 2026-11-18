from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.session import make_engine
from app.ingestion.loader import ingest_file
from app.models import Base, DeviceSnapshot


def _db():
    e = make_engine("sqlite://")
    Base.metadata.create_all(e)
    return sessionmaker(bind=e, expire_on_commit=False)()


def _records(n=3):
    lines = Path(get_settings().dataset_path).read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines[:n]]


def _write(tmp_path, rows) -> Path:
    p = tmp_path / "t.ndjson"
    p.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows), encoding="utf-8")
    return p


def test_reingestion_is_idempotent(tmp_path):
    s = _db()
    p = _write(tmp_path, _records(5))
    a = ingest_file(s, p)
    b = ingest_file(s, p)
    assert (a.inserted, b.inserted, b.skipped_existing) == (5, 0, 5)
    assert s.scalar(select(func.count()).select_from(DeviceSnapshot)) == 5


def test_malformed_records_are_reported_not_inserted(tmp_path):
    s = _db()
    good = _records(2)
    bad_mem = dict(good[0], collected_at="2026-05-20T00:00:00Z")
    bad_mem["memory"] = dict(bad_mem["memory"], used_memory_bytes=10**15)  # used > total
    no_tz = dict(good[0], collected_at="2026-05-21T00:00:00")
    future = dict(good[0], collected_at="2099-01-01T00:00:00Z")
    bad_status = json.loads(json.dumps(good[0]))
    bad_status["collected_at"] = "2026-05-22T00:00:00Z"
    bad_status["compliance_results"][0]["status"] = "maybe"
    stats = ingest_file(s, _write(tmp_path, [*good, "{not json", "", bad_mem, no_tz, future, bad_status]))
    assert stats.inserted == 2
    assert stats.malformed == 5
    assert stats.blank_lines == 1
    kinds = {e["kind"] for e in stats.errors}
    assert kinds == {"invalid_json", "schema"}


def test_duplicates_and_conflicts(tmp_path):
    s = _db()
    r = _records(1)[0]
    changed = json.loads(json.dumps(r))
    changed["memory"]["used_memory_bytes"] -= 1
    stats = ingest_file(s, _write(tmp_path, [r, r, changed]))
    assert (stats.inserted, stats.duplicates_in_file, stats.conflicts) == (1, 1, 1)
    # A later file with different content for a stored snapshot must not overwrite history.
    stats2 = ingest_file(s, _write(tmp_path, [changed]))
    assert stats2.conflicts == 1 and stats2.inserted == 0
    stored = s.scalar(select(DeviceSnapshot))
    assert stored.memory_used_bytes == r["memory"]["used_memory_bytes"]


def test_ownership_violation_rejected(tmp_path):
    s = _db()
    r = _records(2)
    hijack = json.loads(json.dumps(r[1]))
    hijack["company_id"] = "globex-002"
    stats = ingest_file(s, _write(tmp_path, [r[0], hijack]))
    assert stats.ownership_violations == 2 and stats.inserted == 0


def test_missing_optional_fields_handled(tmp_path):
    s = _db()
    r = _records(1)[0]
    r["battery"] = None
    r.pop("network")
    stats = ingest_file(s, _write(tmp_path, [r]))
    assert stats.inserted == 1
    snap = s.scalar(select(DeviceSnapshot))
    assert snap.battery_reported is False and snap.network_reported is False
    assert stats.missing_optional == {"battery": 1, "network": 1}

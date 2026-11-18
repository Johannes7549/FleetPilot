# Data

`device-telemetry-dataset.ndjson` — the assessment dataset: 750 point-in-time snapshots (one JSON object per
line) for 25 devices across 3 companies, 2026-05-14 → 2026-06-12. Schema and validated statistics:
[`docs/discovery.md`](../docs/discovery.md).

Load it (migrations + idempotent ingestion + demo users):

```bash
python scripts/ingest_dataset.py            # uses FLEETPILOT_DATABASE_URL
python scripts/ingest_dataset.py --file other.ndjson
```

The loader prints ingestion statistics (inserted, skipped as already present, in-file duplicates, conflicts,
malformed lines with line numbers and reasons, ownership violations, missing optional blocks). Running it again
inserts nothing.

# Grounding strategy

**Principle: the LLM never produces facts.** Numbers, device lists, classifications and evidence come from
deterministic SQL/pandas code; the LLM (when enabled) may only choose read tools and phrase the result —
and its phrasing is verified.

## 1. Evidence is structured data produced by tools

```json
{
  "evidence_id": "ev_3f1c…",                 // sha1 of (company, device, snapshot, metric, scope, value)
  "device_id": "8NM23J95R5I6",
  "company_id": "acme-001",
  "snapshot_id": "5b0e…",                    // FK to device_snapshots.id
  "collected_at": "2026-06-12T12:02:00Z",
  "metric": "primary_disk_available_pct",
  "observed_value": 2.59,
  "threshold": 10.0,
  "comparator": "<",
  "unit": "%",
  "source": "device_snapshots",              // or compliance_results / installed_software / a tool name
  "temporal_scope": "latest",                // latest | historical | window_aggregate
  "note": "2.6 GB free of 99.9 GB"
}
```

* `temporal_scope` distinguishes **current** measurements (latest snapshot) from **historical** ones
  (e.g. first value in the window, the date a condition changed) and **window aggregates**
  (`23/30 snapshots below 10%`).
* Trend findings carry evidence for the latest point, the earliest point and the aggregate, so a reader can
  verify "17.27% → 2.59%, −0.51 pp/day" from the rows themselves.
* An empty result is still evidence: the validator records a `<tool>:matching_records = 0` item, so
  "0 devices fail high-severity checks" is a grounded statement, not an absence of evidence.

## 2. Evidence is validated before an answer is returned

`validate_evidence` re-queries the database for the caller's tenant and drops anything that is from another
company, references a snapshot that doesn't exist in the tenant, or references an unknown device. The tool
layer also refuses to return evidence whose `company_id` differs from the session (`tenant_violation`).

## 3. Answers

* **Deterministic mode (default):** the answer is assembled from each tool's deterministic `summary`
  (e.g. *"2 of 10 devices are below 10.0% free disk … MT7PJB7N5LRE 2.0% (persistent), 8NM23J95R5I6 2.59%
  (persistent_and_deteriorating)"*). It cannot contain anything the tools didn't compute.
* **LLM mode:** the composer sees sanitised tool output and must return `{answer, cited_evidence_ids}`.
  `verify_grounding` rejects the answer if it mentions any device or employee ID, or any number, that does not
  occur in the tool results, or cites a non-existent evidence ID. Rejected → deterministic answer is used and
  the timeline records `deterministic_after_grounding_failure` with the issues.
* The UI's Evidence Explorer renders the evidence list (not LLM citations) and can open the raw stored
  snapshot behind any item (`GET /api/evidence/snapshots/{id}`, tenant-scoped).

## 4. Honesty rules encoded in code

| Rule | Where |
|---|---|
| Missing telemetry ≠ failure (`battery: null`, absent `network`) | `analyze_battery`: `no_battery_reported` / `latest_snapshot_missing_battery`; risk engine scores 0 with an explanatory factor |
| One-off spike ≠ persistent problem | `trends.classify`: `one_time` / `intermittent` / `persistent` by breach ratio |
| Inference ≠ confirmed fact | risk `issue_type`: `confirmed_failure` vs `deteriorating` vs `persistent` vs `policy` |
| Policy ≠ dataset fact | thresholds, OS minimums and the software watch-list live in `RiskConfig`; answers footnote that thresholds are policy |
| Unsupported questions are declined | `unsupported_data` intent (CPU, uptime, warranty, location, cost, HR data, …) |
| Unknown check IDs are called out | `get_compliance_status` reports "check X does not exist (known checks: …)" |
| No absolute battery health | design capacity is absent from the data; we report condition, cycles and *relative* capacity change and say so |

# Phase 1 — Discovery Report

All figures below were computed independently from `data/device-telemetry-dataset.ndjson`
(profiling script logic is reproduced in `backend/tests/test_dataset_facts.py`, which asserts
every number here against the ingested database).

## 1. Assessment requirements (authoritative, from the PDF)

| Area (rubric weight) | What is required |
|---|---|
| Agent design (25%) | Planning, tool calling, explicit agent loop — "a simple prompt wrapper is insufficient". LangGraph preferred. |
| Grounding & correctness (20%) | Answer from telemetry only; answers traceable; evidence cited; no unsupported conclusions. |
| Action quality & guardrails (20%) | Propose actions via tools (`create_upgrade_order`, `open_remediation_ticket`, `flag_device_for_replacement`, `notify_employee`); tenant isolation; no cross-company access; explicit human approval; audit logs; refuse actions without sufficient evidence. |
| Evaluation rigor (20%) | Runnable evaluation suite; reasonably deterministic execution. |
| Engineering & communication (15%) | Git repo; README covering setup, running agent, evals, architecture, tool catalog, grounding, guardrails, design decisions/trade-offs. |

Insights must include: the finding, supporting evidence, brief explanation.

## 2. Actual schema (every record has these; nested shapes verified across all 750 rows)

```
device_id, company_id, employee_id, collected_at (ISO-8601 Z), agent_version
os:               platform, product_name, product_version, build_version, architecture,
                  kernel_name, kernel_release, hostname
device_identity:  serial_number, model_name, model_identifier, processor, hardware_uuid,
                  total_memory ("16 GB" string)
memory:           ram_bytes, total_memory_bytes, used_memory_bytes, free_memory_bytes, page_size_bytes
disk_volumes[]:   volume_name, file_system, mount_point, size_bytes, available_bytes, encrypted
battery | null:   battery_present, charging_status, percentage, condition, cycle_count, full_charge_capacity
network[] | absent: address, family, interface_name, internal, mac
installed_software[]: name, version, publisher
compliance_results[]: check_id, status (pass|fail), severity (high|medium)
```

## 3. Validated statistics

| Metric | PDF says | Dataset actual |
|---|---|---|
| Records / snapshots | ~750 | **750** (0 malformed, 0 duplicate `(device_id, collected_at)`) |
| Companies | 3 | **3** — `acme-001` (10 devices), `globex-002` (8), `initech-003` (7) |
| Devices | 25 | **25**, exactly 30 snapshots each, one snapshot per day |
| Employees | ~18 | **14** (discrepancy with PDF — we report the dataset figure) |
| Duration | 30 days | **2026-05-14T08:40Z → 2026-06-12T11:00Z** (30 calendar days) |
| Agent version | – | `42.0.0` only |

Ownership is stable: no device ever changes company or employee. Two employees own 3 devices each
(`emp-acme-1003`, `emp-initech-1000`).

### Brief's preliminary benchmark claims — all verified

| Claim | Verified | Devices |
|---|---|---|
| 10 devices on macOS < 15 | ✅ | 13.6: 7VP16KHV88LM, 4SEFDLPTKXAM, SPKRRDF0L786, XZYK11QTPWEC · 14.5: 1LYSSFD074BB, MT7PJB7N5LRE, XSU9UUDGU6AC, QPZSXKINA7GP, DARFCP0BQM2G, RLD7WV7J82SH |
| 6 devices < 10% free disk (latest) | ✅ | 8NM23J95R5I6, MT7PJB7N5LRE (acme) · E4V0SB5I0MYU, JRZSGXVMKE6M (globex) · DARFCP0BQM2G, RLD7WV7J82SH (initech) |
| 4 devices persistently > 80% RAM | ✅ | M4XVHUV1MEPZ (30/30 snapshots), I3JLKILKQTAC (30/30), JRZSGXVMKE6M (29/30), CUXW0BQSV1B7 (28/30) |
| 3 devices battery "Service Recommended" (latest) | ✅ | 7VP16KHV88LM, 8NM23J95R5I6, M4XVHUV1MEPZ (all acme) |
| 0 failed high-severity checks | ✅ | `disk_encryption` (the only high-severity check) passes in all 750 snapshots |
| 19 devices failing `os_up_to_date` (latest) | ✅ | acme 9, globex 4, initech 6 |

## 4. Temporal patterns present in the data

* **Deteriorating storage** (linear decline ≈0.35–0.5 pp/day): 8NM23J95R5I6 (17.3%→2.6%),
  E4V0SB5I0MYU (15.9→2.4), JRZSGXVMKE6M (12.2→2.0), DARFCP0BQM2G (12.1→2.0).
  **Persistently low** (all 30 snapshots < 10%): MT7PJB7N5LRE, RLD7WV7J82SH (bottoming out at 2.0%).
* **Battery degradation**: condition flips Normal → Service Recommended mid-window on
  7VP16KHV88LM (day 17), 8NM23J95R5I6 (day 10), M4XVHUV1MEPZ (day 11); these three also show
  falling `full_charge_capacity` (−4.2%, −4.2%, −4.2%) and cycle counts > 900. All others have flat capacity.
* **Compliance drift** (`screen_lock` pass → fail, never recovers) on 10 devices:
  1LYSSFD074BB, 2MAN6T4DJD95, 8NM23J95R5I6, CUXW0BQSV1B7, DLT35MHGTH2D, DSYPSES7SKBB,
  JRZSGXVMKE6M, MT7PJB7N5LRE, SPKRRDF0L786, XZYK11QTPWEC. `os_up_to_date` never changes per device.
* **Software appearing mid-window**: uTorrent (1LYSSFD074BB, I3JLKILKQTAC, Q1AFVLH3YV33, W2656QDAE2ZJ),
  TeamViewer (DARFCP0BQM2G), CleanMyMac X (7VP16KHV88LM). Whether these are "unapproved" is a
  **policy decision**, not a dataset fact — FleetPilot makes the watch-list configurable.
* **OS versions never change** within the window (no upgrades observed).

## 5. Missing / inconsistent values

* `battery` is `null` in 100 snapshots: the 3 Mac minis (desktop, no battery — 90 snapshots) plus
  intermittent gaps on laptops (MT7PJB7N5LRE ×6 including its **latest** snapshot, 9MXJVZ1I0DKA ×2,
  DARFCP0BQM2G ×1, E4V0SB5I0MYU ×2). A missing battery block is reported as *unknown*, never as failure.
* `network` key is absent in 16 snapshots (intermittent) — treated as "not reported".
* Battery data has no design capacity, so a true "health %" cannot be computed; we use condition,
  cycle count and relative full-charge-capacity change instead (documented limitation).
* No `high`-severity compliance failures exist — questions about them must correctly return "none".
* Hardware `total_memory` is a display string; `memory.total_memory_bytes` is used for math.

## 6. Capabilities the data supports / does not support

Supported: storage, RAM, battery, OS currency, compliance (3 checks), installed software, device↔employee
ownership, 30-day trends. **Not supported**: CPU usage, uptime/crashes, warranty/purchase dates,
employee names/emails, location, patch inventory beyond OS version, network performance. The agent
must say so when asked.

## 7. Implementation checklist

- [x] Discovery + verified benchmark facts
- [x] Monorepo, config, SQLAlchemy models, Alembic migration, idempotent ingestion with stats
- [x] Tenant-scoped repository layer + deterministic analytics (trends, risk scoring, insights)
- [x] Typed tool registry (11 read tools, 4 action tools) with evidence references
- [x] LangGraph agent: guard → intent → plan → execute (loop) → validate evidence → respond / propose
- [x] Action proposals, approval (hash-bound, expiring, idempotent), simulated execution, audit log
- [x] FastAPI routes; Next.js UI (Overview, Copilot, Devices, Insights, Actions, Agent Activity, Settings)
- [x] Evaluation suite (factual, grounding, temporal, safety, tool selection, failure) + report
- [x] Docker Compose, docs, README

# Evaluation methodology & results

FleetPilot has **two separate suites**, reported independently:

| Suite | Command | Report | What it measures |
|---|---|---|---|
| Deterministic (65 cases) | `python scripts/run_evaluations.py` | `evaluation/results/latest.md` | The agent without an LLM: facts, grounding, temporal reasoning, safety, tool selection, failure handling |
| LLM (43 cases) | `python scripts/run_llm_evaluations.py [--live]` | `evaluation/results/llm_latest.md` | The LLM-driven path: interpretation, multi-step planning, tool/argument selection, multi-turn, unsupported/ambiguous requests, invalid model output, provider failures, prompt injection, cross-tenant attempts, evidence hallucination, approval bypass |

Deterministic results are never reported as LLM results.

## Deterministic suite

```bash
python scripts/run_evaluations.py              # deterministic agent, in-memory SQLite, ~20 s, no API cost
python scripts/run_evaluations.py --db postgresql+psycopg://user:pw@host/dedicated_test_db
python scripts/run_evaluations.py --llm        # planner/composer via FLEETPILOT_LLM_PROVIDER (costs tokens)
python scripts/run_evaluations.py --only safety
```

Writes `evaluation/results/latest.md` (human-readable) and `latest.json`; exits non-zero on any failure (CI-ready).

## Design

* **Expected values are independent of the system under test.** They come from the discovery profiling of the
  raw NDJSON (`docs/discovery.md`), not from FleetPilot's own output.
* **Deterministic by default.** No LLM-as-judge. Checks are exact set equality on device IDs, exact counts,
  exact dates, status codes and DB state (audit rows, proposal status).
* **End-to-end where it matters.** Factual, temporal, tool-selection and most safety cases drive the full
  LangGraph agent (guard → classify → plan → execute → validate → propose → respond) per tenant.
* **Isolation.** Mutating cases (approvals, tampering, injection) each get a fresh database.
* **LLM resilience is tested without an LLM**: a scripted provider simulates outages, hallucinated answers,
  invalid tool plans and smuggled tenant arguments.

## Categories (65 cases)

| Category | # | What is checked |
|---|---|---|
| factual | 14 | Low disk per tenant, macOS < 15 per tenant, persistent RAM per tenant, battery-service employees, zero high-severity failures, overview counts, os_up_to_date failures, software lookup |
| grounding | 6 | Every answer has evidence; evidence exists in DB and belongs to the caller's company; evidence values equal DB values; deterministic answers pass the grounding verifier; every insight has finding/evidence/devices/timestamps/explanation/severity/next step; unknown device → no fabricated evidence |
| temporal | 8 | Persistent+deteriorating storage, one-time spike vs persistent, persistence despite dips, battery onset dates, compliance regression dates, historical vs current values, missing telemetry = unknown, software first-seen dates |
| safety | 15 | Cross-tenant by name and by ID, smuggled `company_id`, approval via chat, viewer proposals, insufficient evidence, double execution, rejected never executes, hash mismatch & tampering, expiry, other-tenant approval, invalid device, injection in request, injection in telemetry, audit chain contents |
| tool_selection | 14 | 12 representative questions → exact expected tool sequence; action-vs-question disambiguation |
| failure | 8 | Empty results, oversize/malformed input, LLM outage, hallucination rejection, transient DB error with bounded retry, tool crash containment, downstream adapter failure, malformed dataset lines |

## Latest results

| Run | Result |
|---|---|
| Deterministic, SQLite | **65/65 (100%)** — all six categories 100% |
| Deterministic, PostgreSQL 16 (shared DB; mutating cases on isolated SQLite) | **65/65 (100%)** |
| Unit + integration tests (`pytest`) | SQLite: 105 passed, 4 skipped (PostgreSQL-only RLS tests); PostgreSQL 16: 109 passed |

The full per-case table is in `evaluation/results/latest.md`.

Two genuine defects were found by the suite during development and fixed: *"Which devices are critical?"* was
classified out-of-scope (risk-class questions had no topic), and the software answer omitted device IDs when
filtering by package name.

## LLM suite

```bash
python scripts/run_llm_evaluations.py            # mocked provider: real SDKs over a scripted HTTP transport, no key
python scripts/run_llm_evaluations.py --live     # + the configured real provider (FLEETPILOT_LLM_PROVIDER + key)
python scripts/run_llm_evaluations.py --only multi_step
```

* **Mocked mode** builds real `anthropic.Anthropic` / `openai.OpenAI` clients on an `httpx` mock transport
  (`app/evaluation/llm_mock.py`). FleetPilot's provider code, request construction, SDK retries, error mapping, JSON
  parsing and schema checks all run for real; only the model's *content* is scripted (including deliberately wrong,
  malformed or malicious outputs). This verifies FleetPilot's handling of model behaviour. It does **not** measure a
  real model's quality.
* **Live mode** runs the same questions and model-agnostic expectations against the configured provider (cases that
  need a scripted failure, e.g. HTTP 429 or invalid JSON, are skipped). If no key is configured the report says
  **NOT EXECUTED** with the reason.
* Each case is scored on five dimensions: **tool selection** (required/allowed tools and arguments), **task
  completion** (status, expected devices in validated evidence, proposals), **grounding** (no cross-tenant
  evidence or IDs, answer verified or replaced), **safety** (nothing executed, only pending proposals, no
  unexpected proposals) and **reliability** (no errors, expected execution mode/fallback, bounded LLM calls).
* A *rules-only baseline* column runs the same question with no LLM, showing where model interpretation changes
  the outcome (e.g. L01 "basically out of room": rules give `cannot_answer`; with the LLM plan it is answered with
  the 2 low-disk devices).

| Category | Cases | Examples |
|---|---|---|
| complex_nl | 4 | colloquial storage/RAM/software/OS questions |
| multi_step | 4 | dependent follow-up via result review; two-part questions |
| tool_args | 3 | custom threshold, check-name normalisation, device + metric |
| multi_turn | 3 | "its battery", "orders for those", "their owners" |
| unsupported | 3 | CPU, warranty, unrelated request |
| ambiguous | 3 | clarification, broad question, clarification with invented ID |
| invalid_output | 5 | non-JSON, schema violation, unknown/action tools + tenant arg, out-of-range args, truncation |
| provider_failure | 6 | 429 retried, persistent 529, timeout, 401 circuit breaker, OpenAI success, OpenAI refusal |
| prompt_injection | 3 | injection in request, in telemetry, compromised plan with `company_id` |
| cross_tenant | 2 | other tenant's device ID, action hint targeting another tenant |
| evidence_hallucination | 4 | invented number, fabricated citation, foreign ID, grounded positive control |
| approval_bypass | 3 | "approve all", "execute without approval", model plans an action tool |

### Latest LLM results

| Run | Result |
|---|---|
| Mocked provider (Anthropic + OpenAI SDKs, scripted transport) | **43/43**. Tool selection 34/34, task completion 43/43, grounding 43/43, safety 43/43, reliability 43/43 |
| Live provider (Anthropic) | **12/12** on `claude-opus-5-5` (live Anthropic API, representative subset across 9 categories; 26 LLM calls, ~137k tokens). All five dimensions 100%. Cases: L01, L02, L05, L06, L09, L12, L15, L19, L32, L33, L35, L42 |
| Live provider (OpenAI) | Not run live; covered by the mocked suite (L30, L31) |

The live subset was chosen for cost: one or two cases per category that is meaningful with a real model (cases that
need a scripted failure, such as HTTP 429 or invalid JSON, only exist in mocked mode). Run the whole live suite with
`python scripts/run_llm_evaluations.py --live`, or any subset with `--live-cases`.

## Limitations

* The live run covers a 12-case subset on one model (`claude-opus-5-5`); real-model results can vary between runs
  and models. Safety does not depend on the model: plans are validated, actions need approval and answers are
  verified or replaced.
* The dataset contains no one-off RAM spikes, so spike classification is evaluated on a synthetic series (T02,
  plus unit tests); all other temporal cases use real data.
* Evaluation questions are phrased by the author; a broader paraphrase set would better measure the
  rule-based classifier's recall.

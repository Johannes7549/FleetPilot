# Key design decisions & trade-offs

| # | Decision | Why | Trade-off |
|---|---|---|---|
| 1 | **Deterministic analytics; LLM limited to planning and phrasing** | Fleet statistics must be exact and reproducible; LLMs miscount. The rubric weights grounding/correctness and reproducibility. | Less linguistic flexibility in deterministic mode; mitigated by an optional LLM planner/composer behind validation. |
| 2 | **Agent works fully without an LLM** (rule-based intent + planner + composer) | Runnable/testable with no paid API, deterministic evaluation, graceful degradation on provider outage. | Rule-based NLU covers the domain's question shapes, not arbitrary phrasing. |
| 3 | **Single LangGraph workflow with specialised nodes, not multi-agent** | The problem is structured telemetry analysis; extra agents would add latency and failure modes without capability. | — |
| 4 | **Explicit, bounded tool loop (one tool per iteration) instead of free ReAct** | Observable timeline, hard budgets, every call validated; easy to reason about safety. | Fewer opportunistic multi-hop explorations. |
| 5 | **Server gathers action evidence; LLM-supplied justification is ignored** | An LLM cannot talk the system into an action; evidence rules are code. | Action rules must be maintained per action type. |
| 6 | **Persisted approval state machine (DB) instead of LangGraph `interrupt()`** | Approvals happen later, by another person, after restarts; the CAS update makes double-execution impossible; history is queryable. | The agent run ends at "approval requested" rather than literally pausing. |
| 7 | **Payload-hash-bound approvals** | Guarantees the approved action = the reviewed action + evidence; detects post-creation edits. | Changing a proposal requires a new proposal. |
| 8 | **Hash-chained, append-only audit log** (ORM hook + Postgres trigger) | Tamper-evidence without extra infrastructure. | Not a substitute for WORM storage in regulated settings. |
| 9 | **Normalised schema with denormalised hot columns** | Child tables keep every nested record; hot metrics on `device_snapshots` keep fleet queries simple and fast. | Slight duplication, written once at ingestion. |
| 10 | **Snapshot ID = hash(device, timestamp)** | Idempotent re-ingestion; conflicting re-sends detected, history never overwritten. | Assumes one snapshot per device per timestamp (true in the data). |
| 11 | **SQLite for tests/evals, PostgreSQL for runtime** (portable SQLAlchemy types; JSONB variant) | Fast, service-free test runs; the same suites were also run against PostgreSQL 16 (all passing). | Postgres-only features (trigger, statement timeout) are verified separately. |
| 12 | **No RAG / vector DB / Redis / queue** | Data is structured and small; SQL + pandas answers precisely. | Would revisit for free-text sources (tickets, docs). |
| 13 | **Thresholds/watch-lists as per-tenant policy** | Separates dataset observations from policy decisions; admins can tune without code. | Changing policy changes answers — so settings changes are audited. |
| 14 | **Simulated action adapter, explicitly labelled** | No credentials for real ticketing/procurement; results say `simulated: true` and refs are `SIM-…`. | Real adapters must implement `ActionAdapter.execute`. |
| 15 | **Next.js proxies `/api` to FastAPI** | Same-origin requests: no CORS in the browser, backend URL not exposed. | Requires the Next server (not a static export). |
| 16 | **Demo auth via signed tokens for seeded users** | Lets reviewers switch tenant/role instantly; tenant still comes from the server-side user record. | Must be replaced by an IdP; disabled when `FLEETPILOT_ENVIRONMENT=production`. |

| 17 | **LLM in plan + review + respond, never in computation or execution** | Real LLM-driven interpretation and orchestration (unusual phrasings, decomposition, dependent follow-up steps, multi-turn references) while numbers, evidence, tenancy and approvals stay deterministic. | More LLM calls per question (≤ 4 with one follow-up round); `FLEETPILOT_LLM_STAGES=plan` (hybrid) drops the composer call. |
| 18 | **Result review instead of free ReAct for multi-step** | Dependent steps are requested after inspecting validated results, re-validated like plans, bounded (≤ 3 per round, ≤ 2 rounds, tool budget); the timeline stays a clean sequence. | One extra LLM call per run, even when the first results are already complete. |
| 19 | **Per-run execution mode + fallback reporting** | "LLM mode" is never claimed silently: each run states which stages used the model and which fell back, and the UI shows provider/model and warnings. | Slightly larger run records. |
| 20 | **Mocked LLM evaluation below the SDK** (httpx mock transport) | Exercises real provider code, SDK retries and error mapping without keys or cost; scripted outputs include malicious and malformed cases a real model rarely produces on demand. | Says nothing about real-model quality — the live mode exists for that and is reported separately. |
| 21 | **PostgreSQL RLS via `SET LOCAL ROLE` + transaction-local setting** | Database-enforced backstop that also works when the app connects as a superuser (as the postgres image does); cannot leak across pooled connections; SQLite unaffected. | System sessions remain unrestricted by design; the `users` table is excluded for the demo login. |

## Dataset-driven choices

* Employees: the PDF says ~18, the data has 14 — we report 14.
* No high-severity compliance failures exist; the system answers "0" with evidence of the query rather than
  inventing failures.
* `battery: null` on desktops and intermittently on laptops → "unknown", never "failed".
* No design capacity → no absolute battery health %; relative capacity change is used instead and the
  limitation is stated in the insight text.
* OS versions never change in the window → "No OS version changes observed" rather than a fabricated trend.

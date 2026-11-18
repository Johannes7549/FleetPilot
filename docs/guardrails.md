# Guardrails

| Threat | Control | Verified by |
|---|---|---|
| Cross-tenant data access | Tenant comes only from the signed bearer token → `users.company_id`. Every repository query filters by it; cross-tenant IDs behave exactly like unknown IDs (404, no existence leak). Composite FKs prevent cross-tenant rows. Tools reject `company_id`/`tenant_id`/… arguments; LLM-planned args are stripped of them. Evidence with a foreign `company_id` is rejected by the tool layer and dropped by the validator. Input guard refuses requests naming another company. | `test_tools`, `test_api::test_tenant_isolation_over_http`, evals S01–S03, S11 |
| Cross-tenant access if a query forgets its filter | **PostgreSQL row-level security** (migration `0002`, `app/db/rls.py`): `tenant_isolation` policy (USING + WITH CHECK) on all 12 tenant-owned tables. Each authenticated request's transactions run as the unprivileged `fleetpilot_app` role (superusers/owners would bypass RLS) with `fleetpilot.company_id` set transaction-locally, so it cannot leak through the connection pool. Unbound system sessions (ingestion, migrations) see all rows. No-op on SQLite. | `test_rls.py` (PostgreSQL), API flow verified on PostgreSQL 16 |
| Unauthorised actions | Role permissions: `viewer` (read), `operator` (+propose), `admin` (+approve, settings). Checked in the tool registry, the action service and the API dependency — three independent places. | `test_actions`, evals S05 |
| Actions without human approval | Action tools only create `pending_approval` proposals. There is no code path from the agent to execution. Approval is an API call by an authorised human. Chat requests to approve/execute/bypass are refused by the input guard. | evals S04, `test_agent::test_guard_blocks` |
| Approving the wrong thing | Each proposal stores `payload_hash = sha256(company, action, params, evidence_ids)`. The approver must submit the hash they reviewed; the server also recomputes it from stored fields, so a proposal whose params **or evidence** were modified after creation cannot be approved. | `test_hash_mismatch_refused`, `test_modified_proposal_cannot_be_approved`, `test_security::test_modified_params/evidence_*`, `test_http_tampered_and_stale_hash_and_roles`, eval S09 |
| Double / concurrent execution | `pending_approval → executing` is a compare-and-swap UPDATE on `(status, version)`, committed before the side effect runs. Repeated/concurrent approvals get 409. Rejected, expired and failed proposals can never move to `executing`. Duplicate open proposals are prevented by a unique `idempotency_key`. | `test_approve_executes_exactly_once`, `test_security::test_concurrent_approvals_execute_exactly_once` (4 threads, separate sessions, file DB → exactly 1 adapter call), `test_duplicate_execution_prevented`, evals S07, S08, FH07 |
| Stale approvals | Proposals expire after `FLEETPILOT_APPROVAL_TTL_HOURS` (default 72) and are marked `expired`; the refusal is recorded in the approval trail. | `test_expired_proposal_cannot_be_approved`, `test_security::test_expired_approval_*`, eval S10 |
| Cross-tenant approval | Proposals are loaded with the approver's `company_id`; another tenant's admin gets 404 and nothing executes. | `test_security::test_cross_tenant_admin_cannot_approve`, `test_api::test_full_approval_flow` |
| Actions without evidence | `action_policy.py` gathers evidence server-side and enforces per-action rules (e.g. memory upgrade needs *persistent* RAM pressure; replacement needs risk ≥ at_risk; remediation needs the check failing in the latest snapshot; battery needs a service condition, capacity decline or high cycles). Evidence snapshots must exist in the tenant. | `test_insufficient_evidence_refused`, eval S06 |
| Prompt injection via request | Input guard pattern screen (ignore instructions, role-play, reveal prompt, approve-all…); request is wrapped as untrusted data for the planner. | eval S13 |
| Prompt injection via telemetry | All string leaves of tool output are sanitised (`guardrails/injection.py`) before the LLM **review** and **respond** stages: control/bidi characters stripped, length capped, instruction-like text replaced with `[REDACTED…]`, wrapped in `<untrusted_…>` blocks the system prompt declares non-executable. Even if text slipped through, the LLM holds no capability it could unlock. | `test_injection_in_telemetry_is_sanitised`, `test_security::test_injection_in_hostname_and_model_never_reaches_llm`, eval S14, LLM eval L33 |
| Compromised / wrong model output | Planner and reviewer output validated: only known **read** tools, tenant args stripped, args schema-checked, step budgets; action hints only fill gaps and still go through tenant + evidence checks + approval; the rule classifier stays authoritative for `action_request` / `unsupported_data`. Provider errors are categorised; a per-run circuit breaker stops calling a failing provider. | `test_llm.py`, LLM evals L15, L21–L25, L34, L36, L43 |
| Hallucinated answers | Grounding verifier (IDs, numbers, citations) with deterministic fallback; LLM clarification questions are stripped of IDs and numbers. | eval FH04, LLM evals L20, L37–L40 |
| Tampering with history | Snapshots are immutable (conflicting re-ingest is reported, never overwrites). Audit log is append-only (ORM hook + PostgreSQL trigger) and hash-chained per company; `GET /api/audit/verify` detects edits. | `test_audit_chain_and_append_only`, eval S15 |
| Resource abuse / runaway loops | Request ≤ 2000 chars, ≤ 8 tool calls, ≤ 2 re-plans (≤ 3 LLM-requested follow-up steps each), ≤ 10 proposals/request, LangGraph recursion limit 40, Postgres `statement_timeout` 15s, tool time budget, LLM timeout + bounded retries, per-user rate limit on `/copilot/ask`. | `test_api::test_rate_limit`, eval FH02 |
| Failures | Tool exceptions → structured `tool_error`; transient DB errors → one retry then `database_error`; LLM errors/refusals/invalid JSON → deterministic fallback; adapter failure → proposal `failed`, audited, not auto-retried. | evals FH03–FH07 |

## Audit trail contents

Every entry: timestamp, company, actor + actor type (user/agent/system), event type, proposal ID, device ID,
details, previous hash, entry hash. Events: `action.proposed` (params, evidence IDs, payload hash,
justification, agent run), `action.approve.accepted|refused`, `action.reject.accepted|refused` (with reason),
`action.executed` (adapter result) / `action.failed`, `action.expired`, `settings.updated` (before/after),
`agent.request_blocked`.

## Known limitations

* Demo authentication (HMAC tokens for seeded users) stands in for an IdP; `FLEETPILOT_ENVIRONMENT=production`
  disables the demo login endpoints.
* The rate limiter is in-process (single API instance).
* Tenant isolation is enforced in the application/query layer, by composite FKs and (on PostgreSQL) by row-level
  security for authenticated requests. Unbound system sessions (ingestion, migrations, the demo user list) are
  intentionally not tenant-restricted, and the `users` table has no RLS policy (the demo login lists all users).
* Separation of duties (`FLEETPILOT_REQUIRE_DISTINCT_APPROVER`) is off by default so a single demo admin can
  exercise the flow; turn it on for real deployments.

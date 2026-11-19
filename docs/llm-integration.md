# LLM integration

FleetPilot runs with or without an LLM. With one configured, the model takes part in **interpretation, planning
and tool orchestration**. It never computes telemetry statistics, invents evidence, chooses the tenant, or executes
anything. Every model output is validated, and every LLM stage has a deterministic fallback.

## Execution modes

| Mode | Set by | LLM stages | What the model does |
|---|---|---|---|
| **Deterministic** | `FLEETPILOT_LLM_PROVIDER=deterministic` (default) | none | Nothing. Rule-based intent, planner and composer. |
| **LLM-powered** | provider `anthropic`/`openai` + key, `FLEETPILOT_LLM_STAGES=all` | plan · review · respond | Interprets the request, plans read tools with arguments, inspects results and asks for follow-up steps, phrases the answer. |
| **Hybrid** | provider + key, `FLEETPILOT_LLM_STAGES=plan` | plan · review | As above, but answers are composed deterministically. This is the cheapest and most conservative LLM option. |
| **Deterministic fallback** | provider selected but unusable (no key, SDK missing), or every LLM call in a run failed | none | Nothing. This is **reported**, not hidden: header badge, Settings card, `/api/system/llm-status`, `/api/health` and every run's `execution` block. |

Each run records how it was actually produced (`response.execution`):

```json
{"mode": "llm|hybrid|deterministic|deterministic_fallback", "provider": "anthropic", "model": "claude-opus-5-5",
 "llm_calls": [{"stage": "plan", "ok": true, "latency_ms": 812, "input_tokens": 3287, "output_tokens": 67}],
 "fallbacks": [{"stage": "respond", "reason": "LLM answer failed grounding verification; deterministic answer used"}],
 "warning": "...", "intent_source": "llm", "plan_source": "llm", "answer_source": "llm_verified"}
```

A run is labelled `llm` only if every LLM stage it attempted succeeded. If any stage fell back, the run is
`hybrid` and the `fallbacks` list says which stage and why. If no LLM call succeeded, the run is
`deterministic_fallback`.

## Where the LLM sits in the graph

```
guard (rules) → classify (rules + multi-turn carry-over)
   → plan        [LLM stage "plan": intent, read steps + args, action hints, clarification]
   → execute ⟲   (one validated tool per iteration; budget FLEETPILOT_AGENT_MAX_TOOL_CALLS)
   → validate    (evidence re-checked against the DB)  [LLM stage "review": complete? follow-up read steps]
        └─ follow-ups → plan (re-plan appends steps; bounded by FLEETPILOT_AGENT_MAX_REPLANS) → execute → validate
   → propose     (rules + server-side evidence policy; human approval required)
   → respond     [LLM stage "respond": phrasing, must pass the grounding verifier]
```

| Stage | Structured output (JSON schema) | Validation applied to the output | On failure |
|---|---|---|---|
| plan | `{intent, steps[{tool, args_json, purpose}], clarification, action{action_type, component, check_id, target_device_ids, target_employee_ids}}` | Unknown tools dropped. **Action tools dropped**. Tenant arguments (`company_id`, …) stripped. Arguments validated against each tool's Pydantic model. Max 6 steps, duplicates removed. Action hints only fill gaps in the rule-extracted entities (enum- and ID-format-checked). Clarification text is stripped of IDs and numbers. | Rule planner (`plan_source=deterministic_fallback`) |
| review | `{complete, missing, follow_up_steps[...]}` | Same step validation. At most 3 follow-ups per round, never a repeat of an executed call, never beyond the tool budget. | Current results are accepted as they are |
| respond | `{answer, cited_evidence_ids[]}` | `verify_grounding`: every device ID, employee ID and number in the answer must occur in the tool output, and every cited evidence ID must be a validated one. | Deterministic answer (`deterministic_after_grounding_failure` / `_after_llm_error`) |

**Safety-relevant decisions stay deterministic.**

- The input guard (cross-tenant references, approval bypass, injection phrasing) runs before any model call.
- If the rule classifier says `action_request` or `unsupported_data`, that decision stands. A model cannot
  downgrade an action request or "answer" a CPU question from unrelated tools.
- A model may *upgrade* a request to `action_request` only by providing a valid action type. The result is still
  only a proposal: the server gathers the evidence, checks tenant ownership and requires approval.
- When an LLM is available, a request the rules classify as `out_of_scope` goes to the LLM planner rather than
  being rejected. This is what lets unusual phrasings work, such as "which laptops are basically out of room?".

**Untrusted text.**

- The user's request and all telemetry strings are placed in `<untrusted_*>` blocks.
- Telemetry is sanitised before the review and respond stages see it: control characters are stripped and
  instruction-like text is replaced with `[REDACTED …]`.
- The tenant ID never appears in a prompt.

## Multi-step and multi-turn

- **Decomposition:** the planner emits one step per part of the request, e.g. "low storage *and* batteries"
  becomes two tools.
- **Dependent steps:** "show the riskiest device's battery history" needs a value from the first result. The
  planner plans `get_risk_scores`; the review stage sees the result and requests
  `get_device_history(device_id=<top device>)`; the re-plan appends it and the loop executes it. Evidence
  validation runs again over everything.
- **Conversation context:** the API rebuilds a conversation's history from this tenant's earlier runs, including
  each earlier answer's device and employee IDs and tools. The planner sees the last 6 turns. Without an LLM, a
  deterministic carry-over resolves "it / its / those / them / what about …" to the previous answer's targets
  (at most 5). The timeline records it as `context_carryover`.

## Providers, timeouts, retries, rate limits

| Concern | Anthropic | OpenAI |
|---|---|---|
| SDK | `anthropic` `messages.create` | `openai` `chat.completions.create` |
| Structured output | `output_config.format = {type: json_schema, schema}`, `effort` from `FLEETPILOT_LLM_EFFORT` (default `low`; empty to omit for older models) | `response_format = json_schema, strict: true`, `max_completion_tokens` |
| Default model | `claude-opus-5-5` | `gpt-4o-mini` |
| Timeout | `FLEETPILOT_LLM_TIMEOUT_SECONDS` (30) | same |
| Retries | SDK retries with exponential backoff (honours `retry-after`) for connection errors, 408/409/429 and 5xx: `FLEETPILOT_LLM_MAX_RETRIES` (2) | same |

Errors are mapped to categories:

- `timeout`
- `rate_limit` (retries exhausted)
- `auth`
- `connection`
- `overloaded`
- `api_error`
- `refusal`
- `truncated`
- `invalid_json`
- `schema_violation` (the JSON is re-checked locally even though the provider enforces the schema)

A per-run circuit breaker (`GuardedLLM`) opens after an `auth` error or after two failed calls, so later stages
skip straight to the deterministic path instead of waiting on timeouts again. The last provider error is surfaced
in `/api/system/llm-status`.

## Observability

Agent Activity shows, per run:

- execution mode, provider and model
- intent and its source (rules or LLM)
- plan source and answer source
- the plan as a table (tool, arguments, purpose)
- every LLM call: stage, result or error category, model, latency, input and output tokens
- fallbacks
- evidence-validation checks: tenant match, snapshots exist, devices belong to the tenant
- the step timeline, where each tool call shows its arguments, a bounded result preview, attempts, duration and
  evidence count

Prompts' sizes are recorded, not their text. No chain-of-thought is requested, stored or shown.

## Verification status

- **Mocked provider: verified.** The real provider classes and real SDK clients run over a scripted HTTP
  transport (`app/evaluation/llm_mock.py`). This exercises request construction, SDK retries, HTTP error mapping,
  parsing and the agent's handling of good, bad and malicious model output. Results: `tests/test_llm.py`, and
  `scripts/run_llm_evaluations.py` with 43 cases.
- **Live provider (Anthropic): verified.** 12/12 live cases passed on `claude-opus-5-5` (26 LLM calls, ~137k
  tokens): colloquial interpretation, two-part decomposition, a dependent follow-up through result review,
  argument generation, a multi-turn reference, unsupported data, an ambiguous request, telemetry injection,
  cross-tenant and approval-bypass attempts. Every model-written answer passed the grounding verifier. A typical
  answered question costs 3 calls (plan, review, respond), or 4 with one follow-up round. Results:
  `evaluation/results/llm_latest.md` (live section).
- **Live provider (OpenAI):** not run live; the provider is covered by the mocked suite.
- `python scripts/run_llm_evaluations.py --live [--live-cases …]` re-runs the live evaluation against the configured
  model. Without a key, the live section is marked **NOT EXECUTED** and gives the reason.

export type Classification = "critical" | "at_risk" | "watch" | "healthy";

export interface Evidence {
  evidence_id: string;
  device_id: string | null;
  company_id: string;
  snapshot_id: string | null;
  collected_at: string | null;
  metric: string;
  observed_value: unknown;
  threshold: number | string | null;
  comparator: string;
  unit: string | null;
  source: string;
  temporal_scope: "latest" | "historical" | "window_aggregate";
  note: string | null;
}

export interface Me {
  user_id: string;
  display_name: string;
  role: "viewer" | "operator" | "admin";
  company: { id: string; name: string };
  permissions: string[];
}

export interface DemoUser {
  username: string;
  display_name: string;
  role: string;
  company_id: string;
}

export interface CategoryScore {
  score: number;
  factors: string[];
  issue_type: string;
}

export interface DeviceRisk {
  device_id: string;
  employee_id: string | null;
  hostname: string | null;
  model_name: string | null;
  overall_score: number;
  classification: Classification;
  categories: Record<string, CategoryScore>;
  top_category: string | null;
  contributing_factors: string[];
  recommended_action: { tool: string; args: Record<string, unknown>; why: string } | null;
  evidence?: Evidence[];
}

export interface Dashboard {
  company: { id: string; name: string };
  generated_from: { snapshots: number; window_start: string; window_end: string };
  totals: Record<"devices" | "employees" | "healthy" | "watch" | "at_risk" | "critical" | "pending_approvals", number>;
  storage_health: Record<string, number>;
  memory_health: Record<string, number>;
  battery_health: Record<string, number>;
  compliance: { check_id: string; severity: string; pass: number; fail: number }[];
  os_distribution: Record<string, number>;
  trends: {
    day: string;
    avg_disk_available_pct: number;
    avg_memory_used_pct: number;
    devices_low_storage: number;
    devices_high_memory: number;
    battery_service_recommended: number;
    compliance_fail_rate_pct: number;
  }[];
  top_risks: DeviceRisk[];
  thresholds: { low_storage_pct: number; critical_storage_pct: number; memory_high_pct: number };
}

export interface DeviceRow {
  device_id: string;
  employee_id: string | null;
  hostname: string | null;
  model_name: string | null;
  processor: string | null;
  total_memory: string | null;
  platform: string | null;
  os: string | null;
  last_seen_at: string | null;
  disk_available_pct: number | null;
  memory_used_pct: number | null;
  battery_condition: string | null;
  battery_reported: boolean;
  failing_checks: string[];
  risk_score: number;
  classification: Classification;
  top_category: string | null;
}

export interface Insight {
  insight_id: string;
  category: string;
  title: string;
  finding: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  pattern_type: string;
  explanation: string;
  device_ids: string[];
  first_observed_at: string | null;
  last_observed_at: string | null;
  recommended_next_step: string;
  recommended_actions: { tool: string; args: Record<string, unknown> }[];
  evidence: Evidence[];
}

export interface ToolUse {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  error: string | null;
  error_code: string | null;
  duration_ms: number;
  kind: string;
}

export interface ProposalSummary {
  proposal_id: string;
  action_type: string;
  params: Record<string, string>;
  status: string;
  created: boolean;
  payload_hash: string;
  justification: string;
  expires_at: string;
}

export interface AgentResponse {
  run_id: string;
  answer: string;
  status: string;
  intent: string | null;
  evidence: Evidence[];
  proposals: ProposalSummary[];
  refused_actions: { action_type: string; params: Record<string, string>; reason: string; code?: string }[];
  tools_used: ToolUse[];
  grounding: { method: string; issues?: string[]; plan_source?: string; replan_sources?: string[]; validation?: Validation | null };
  plan?: PlanStep[];
  intent_source?: string;
  clarification?: string | null;
  context?: Record<string, unknown> | null;
  execution?: Execution;
  guard: { blocked: boolean; reason: string | null; code: string | null } | null;
  duration_ms: number;
  provider: string;
  token_usage: { input_tokens: number; output_tokens: number };
}

export interface Step {
  seq: number;
  step_type: string;
  name: string;
  status: string;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  evidence_ids: string[] | null;
  duration_ms: number | null;
  started_at: string;
}

export interface AgentRun {
  run_id: string;
  question: string;
  intent: string | null;
  status: string;
  provider: string | null;
  conversation_id: string | null;
  user_id: string;
  created_at: string;
  duration_ms: number | null;
  token_usage: { input_tokens: number; output_tokens: number } | null;
  plan: PlanStep[] | null;
  execution?: Execution | null;
  response?: AgentResponse;
  steps?: Step[];
}

export interface ActionProposal {
  id: string;
  action_type: string;
  device_id: string | null;
  employee_id: string | null;
  params: Record<string, string>;
  justification: string;
  evidence: Evidence[];
  payload_hash: string;
  status: string;
  proposed_by: string;
  agent_run_id: string | null;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  decided_by: string | null;
  executed_at: string | null;
  execution_result: Record<string, unknown> | null;
  external_ref: string | null;
  history?: { decision: string; approver_id: string; outcome: string; comment: string | null; created_at: string }[];
}

export interface PlanStep { tool: string; args: Record<string, unknown>; purpose: string }

export interface Validation {
  status: string;
  reason: string | null;
  issues: string[];
  evidence_count: number;
  tools_ok: number;
  tools_failed: number;
  checks?: { tenant_match: boolean; snapshots_exist: boolean; devices_in_tenant: boolean };
}

export interface LLMCall {
  stage: string;
  provider: string;
  model: string;
  ok: boolean;
  latency_ms: number;
  input_tokens?: number;
  output_tokens?: number;
  error_kind?: string;
  error?: string;
  prompt_chars?: number;
}

/** How one run was produced. `mode` never claims LLM execution that did not happen. */
export interface Execution {
  mode: "deterministic" | "llm" | "hybrid" | "deterministic_fallback";
  configured_provider: string | null;
  provider: string | null;
  model: string | null;
  llm_stages_enabled: string[];
  llm_calls: LLMCall[];
  fallbacks: { stage: string; reason: string }[];
  circuit_open: string | null;
  warning: string | null;
  intent_source?: string;
  plan_source?: string;
  answer_source?: string;
}

export interface LLMStatus {
  configured_provider: string;
  active_mode: "deterministic" | "llm" | "hybrid" | "deterministic_fallback";
  provider: string | null;
  model: string | null;
  api_key_configured: boolean | null;
  llm_stages: string[];
  unavailable_reason: string | null;
  last_error: { provider: string; kind: string; message: string; at: string } | null;
  warning: string | null;
  timeout_seconds: number;
  max_retries: number;
}

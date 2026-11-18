"""LLM provider abstraction.

The LLM participates in three narrow, validated stages of the agent:

* ``plan``    - interpret the request and choose read tools + arguments (and action hints),
* ``review``  - inspect tool results and request bounded follow-up read tools when the answer is incomplete,
* ``respond`` - phrase the answer from tool results (discarded unless it passes the grounding verifier).

Every stage also has a deterministic implementation, so the application (and the deterministic evaluation
suite) works with no provider configured. Any provider error, timeout, refusal or schema violation makes that
stage fall back to the deterministic path; it never fails the request. The LLM never computes statistics,
never sees or chooses the tenant, and can never execute a state-changing action.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import LLMProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-4o-mini"}
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class LLMCallError(LLMProviderError):
    """Provider failure with a machine-readable category.

    kind: timeout | rate_limit | auth | billing | connection | overloaded | api_error | refusal | truncated |
          invalid_json | schema_violation | disabled
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class LLMResult:
    data: dict
    usage: dict = field(default_factory=dict)
    model: str = ""
    latency_ms: int = 0


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(self, *, system: str, user: str, schema: dict, max_tokens: int = 4000) -> LLMResult: ...


def check_schema(data: Any, schema: dict, path: str = "$") -> None:
    """Minimal structural validation of model output against our (simple) JSON schemas.

    Providers enforce the schema server-side, but model output is untrusted: re-check required keys,
    types and enums so a malformed response is a categorised error, not a KeyError deep in a node.
    """
    t = schema.get("type")
    py = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if t in py and not isinstance(data, py[t]) or (t in ("integer", "number") and isinstance(data, bool)):
        raise LLMCallError("schema_violation", f"{path}: expected {t}")
    if "enum" in schema and data not in schema["enum"]:
        raise LLMCallError("schema_violation", f"{path}: value not in enum")
    if t == "object":
        for k in schema.get("required", []):
            if k not in data:
                raise LLMCallError("schema_violation", f"{path}.{k}: missing")
        for k, sub in schema.get("properties", {}).items():
            if k in data:
                check_schema(data[k], sub, f"{path}.{k}")
    elif t == "array" and "items" in schema:
        for i, item in enumerate(data):
            check_schema(item, schema["items"], f"{path}[{i}]")


def _api_message(exc: Any) -> str:
    """The provider's own error message (e.g. 'credit balance is too low'), truncated. Never contains the key."""
    body = getattr(exc, "body", None)
    msg = body.get("error", {}).get("message") if isinstance(body, dict) and isinstance(body.get("error"), dict) else None
    return str(msg or getattr(exc, "message", "") or "")[:200]


def _status_kind(exc: Any, default: str) -> str:
    msg = _api_message(exc).lower()
    if "credit balance" in msg or "quota" in msg or "billing" in msg or getattr(exc, "status_code", 0) == 402:
        return "billing"  # account cannot be charged: retrying in this run is pointless
    return default


def _parse(text: str, schema: dict) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMCallError("invalid_json", "model returned invalid JSON") from exc
    check_schema(data, schema)
    return data


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings, client: Any = None) -> None:
        import anthropic

        self.model = settings.llm_model or DEFAULT_MODELS["anthropic"]
        self.effort = settings.llm_effort
        # The SDK retries connection errors, 408/409/429 and 5xx with exponential backoff (max_retries).
        # `client` lets the LLM evaluation suite inject an SDK client backed by a mock HTTP transport.
        self._client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key,
                                                     timeout=settings.llm_timeout_seconds,
                                                     max_retries=settings.llm_max_retries)

    def complete_json(self, *, system: str, user: str, schema: dict, max_tokens: int = 4000) -> LLMResult:
        import anthropic

        t0 = time.perf_counter()
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        try:
            resp = self._client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
                # Structured output: the response text is constrained to JSON matching `schema`.
                output_config=output_config,
            )
        except anthropic.APITimeoutError as exc:
            raise LLMCallError("timeout", "anthropic request timed out") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMCallError("connection", "anthropic connection error") from exc
        except anthropic.RateLimitError as exc:
            raise LLMCallError("rate_limit", "anthropic rate limited (retries exhausted)") from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMCallError("auth", f"anthropic rejected the API key ({exc.status_code})") from exc
        except anthropic.APIStatusError as exc:
            kind = "overloaded" if exc.status_code in (503, 529) else "api_error"
            raise LLMCallError(_status_kind(exc, kind), f"anthropic API error {exc.status_code}: {_api_message(exc)}") from exc
        if resp.stop_reason == "refusal":
            raise LLMCallError("refusal", "model declined the request")
        if resp.stop_reason == "max_tokens":
            raise LLMCallError("truncated", "model output truncated")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = _parse(text, schema)
        return LLMResult(data=data, model=getattr(resp, "model", None) or self.model,
                         latency_ms=int((time.perf_counter() - t0) * 1000),
                         usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens})


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings, client: Any = None) -> None:
        import openai

        self.model = settings.llm_model or DEFAULT_MODELS["openai"]
        self._client = client or openai.OpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds,
                                               max_retries=settings.llm_max_retries)

    def complete_json(self, *, system: str, user: str, schema: dict, max_tokens: int = 4000) -> LLMResult:
        import openai

        t0 = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(
                model=self.model, max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": True}},
            )
        except openai.APITimeoutError as exc:
            raise LLMCallError("timeout", "openai request timed out") from exc
        except openai.APIConnectionError as exc:
            raise LLMCallError("connection", "openai connection error") from exc
        except openai.RateLimitError as exc:
            raise LLMCallError("rate_limit", "openai rate limited (retries exhausted)") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise LLMCallError("auth", "openai rejected the API key") from exc
        except openai.APIStatusError as exc:
            kind = "overloaded" if exc.status_code in (503, 529) else "api_error"
            raise LLMCallError(_status_kind(exc, kind), f"openai API error {exc.status_code}: {_api_message(exc)}") from exc
        except openai.OpenAIError as exc:
            raise LLMCallError("api_error", f"openai error: {exc.__class__.__name__}") from exc
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMCallError("refusal", "model declined the request")
        if choice.finish_reason == "length":
            raise LLMCallError("truncated", "model output truncated")
        if choice.finish_reason != "stop" or not choice.message.content:
            raise LLMCallError("api_error", f"openai finish_reason={choice.finish_reason}")
        data = _parse(choice.message.content, schema)
        usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens} if resp.usage else {}
        return LLMResult(data=data, usage=usage, model=getattr(resp, "model", None) or self.model,
                         latency_ms=int((time.perf_counter() - t0) * 1000))


# ---------------------------------------------------------------------------------------------
# Provider resolution + status
# ---------------------------------------------------------------------------------------------
_last_error: dict | None = None
_lock = threading.Lock()


def record_provider_error(provider: str, kind: str, message: str) -> None:
    global _last_error
    with _lock:
        _last_error = {"provider": provider, "kind": kind, "message": message, "at": datetime.now(UTC).isoformat()}


def clear_provider_error() -> None:
    global _last_error
    with _lock:
        _last_error = None


def _unavailable_reason(s: Settings) -> str | None:
    if s.llm_provider == "deterministic":
        return None
    key = s.anthropic_api_key if s.llm_provider == "anthropic" else s.openai_api_key
    if not key:
        return f"{KEY_ENV[s.llm_provider]} is not set"
    try:
        __import__(s.llm_provider)
    except ImportError:
        return f"the '{s.llm_provider}' Python SDK is not installed"
    return None


def llm_status(settings: Settings | None = None) -> dict:
    """What the UI and API report about the execution mode. Never includes secrets.

    configured_mode: what FLEETPILOT_LLM_PROVIDER/LLM_STAGES ask for.
    active_mode:     deterministic | llm | hybrid | deterministic_fallback
      * llm        - LLM plans, reviews results and phrases answers (all validated)
      * hybrid     - LLM plans and reviews; answers are composed deterministically (FLEETPILOT_LLM_STAGES=plan)
      * deterministic_fallback - a provider was selected but cannot be used (reason given)
    """
    s = settings or get_settings()
    reason = _unavailable_reason(s)
    if s.llm_provider == "deterministic":
        active = "deterministic"
    elif reason:
        active = "deterministic_fallback"
    else:
        active = "hybrid" if s.llm_stages == "plan" else "llm"
    warning = None
    if active == "deterministic_fallback":
        warning = (f"LLM provider '{s.llm_provider}' is selected but {reason}. FleetPilot is running in "
                   "deterministic fallback mode — no LLM is being called.")
    with _lock:
        last = dict(_last_error) if _last_error else None
    if last and active in ("llm", "hybrid"):
        warning = (f"Recent LLM request failed ({last['kind']}); affected stages fell back to deterministic "
                   "execution.")
    return {
        "configured_provider": s.llm_provider,
        "active_mode": active,
        "provider": s.llm_provider if active in ("llm", "hybrid") else None,
        "model": (s.llm_model or DEFAULT_MODELS.get(s.llm_provider)) if s.llm_provider != "deterministic" else None,
        "api_key_configured": bool(s.anthropic_api_key if s.llm_provider == "anthropic" else s.openai_api_key)
        if s.llm_provider != "deterministic" else None,
        "llm_stages": ["plan", "review"] + ([] if s.llm_stages == "plan" else ["respond"])
        if active in ("llm", "hybrid") else [],
        "unavailable_reason": reason,
        "last_error": last,
        "warning": warning,
        "timeout_seconds": s.llm_timeout_seconds,
        "max_retries": s.llm_max_retries,
    }


def get_provider(settings: Settings | None = None) -> LLMProvider | None:
    """Returns None for deterministic mode, or when the selected provider cannot be used (logged loudly
    and reported by `llm_status` / every run's `execution` block — never silently)."""
    s = settings or get_settings()
    if s.llm_provider == "deterministic":
        return None
    reason = _unavailable_reason(s)
    if reason:
        logger.warning(f"LLM provider '{s.llm_provider}' unavailable ({reason}); running in deterministic fallback mode")
        return None
    return AnthropicProvider(s) if s.llm_provider == "anthropic" else OpenAIProvider(s)


# ---------------------------------------------------------------------------------------------
# Per-run wrapper: call log, stage gating and circuit breaker
# ---------------------------------------------------------------------------------------------
NON_RECOVERABLE = {"auth", "billing"}  # no point retrying another stage in the same run


class GuardedLLM:
    """Wraps a provider for one agent run.

    * records every call (stage, outcome, error category, latency, tokens, model) for observability,
    * converts unexpected exceptions into categorised `LLMCallError`s (the agent only handles those),
    * opens a circuit after a non-recoverable error (bad key) or `max_failures` failures, so later stages
      go straight to the deterministic path instead of waiting on timeouts again.
    """

    def __init__(self, provider: LLMProvider, *, stages: set[str], max_failures: int = 2) -> None:
        self.provider = provider
        self.name = getattr(provider, "name", "llm")
        self.model = getattr(provider, "model", "")
        self.stages = stages
        self.max_failures = max_failures
        self.calls: list[dict] = []
        self.failures = 0
        self.disabled_reason: str | None = None

    def enabled(self, stage: str) -> bool:
        return stage in self.stages and self.disabled_reason is None

    def complete_json(self, *, stage: str, system: str, user: str, schema: dict, max_tokens: int = 4000) -> LLMResult:
        if not self.enabled(stage):
            raise LLMCallError("disabled", self.disabled_reason or f"stage '{stage}' not enabled for the LLM")
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"stage": stage, "provider": self.name, "model": self.model,
                               "prompt_chars": len(system) + len(user)}
        try:
            res = self.provider.complete_json(system=system, user=user, schema=schema, max_tokens=max_tokens)
            if not isinstance(res.data, dict):
                raise LLMCallError("schema_violation", "model output is not a JSON object")
            check_schema(res.data, schema)
        except LLMCallError as exc:
            self._fail(rec, exc.kind, exc.message, t0)
            raise
        except LLMProviderError as exc:
            self._fail(rec, "api_error", exc.message, t0)
            raise LLMCallError("api_error", exc.message) from exc
        except Exception as exc:  # noqa: BLE001 - SDK bugs, unexpected payloads
            self._fail(rec, "api_error", f"{exc.__class__.__name__}", t0)
            raise LLMCallError("api_error", f"unexpected provider error: {exc.__class__.__name__}") from exc
        rec.update(ok=True, latency_ms=int((time.perf_counter() - t0) * 1000), model=res.model or self.model,
                   input_tokens=res.usage.get("input_tokens", 0), output_tokens=res.usage.get("output_tokens", 0))
        self.calls.append(rec)
        return res

    def _fail(self, rec: dict, kind: str, message: str, t0: float) -> None:
        rec.update(ok=False, error_kind=kind, error=message[:300], latency_ms=int((time.perf_counter() - t0) * 1000))
        self.calls.append(rec)
        self.failures += 1
        record_provider_error(self.name, kind, message[:300])
        if kind in NON_RECOVERABLE:
            self.disabled_reason = f"LLM disabled for this run after a non-recoverable '{kind}' error"
        elif self.failures >= self.max_failures:
            self.disabled_reason = f"LLM disabled for this run after {self.failures} failed calls"

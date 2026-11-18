"""Mock LLM HTTP backends for the LLM evaluation suite.

The mock sits *below* the official SDKs: a real `anthropic.Anthropic` / `openai.OpenAI` client is built with an
`httpx.MockTransport`, so every mocked case exercises FleetPilot's actual provider code — request construction
(structured-output schema, model, system prompt), SDK retries/backoff, HTTP error mapping, JSON parsing and
schema checks. Only the model's *content* is scripted. This verifies FleetPilot's orchestration, validation,
grounding and safety around a model; it does not measure the quality of any real model.

Script format: {stage: [item, ...]} consumed in order per stage (stage = plan | review | respond), where item is
  dict                         -> returned as the model's JSON output
  callable(user_text) -> dict  -> computed from the prompt (e.g. pick a device from tool results)
  ("http", status)             -> HTTP error response (e.g. 429, 401, 529)
  ("timeout",)                 -> transport timeout
  ("text", raw, stop_reason)   -> raw text content with the given stop reason (invalid JSON, truncation...)
  ("refusal",)                 -> refusal stop reason
Defaults when a stage's queue is empty: review -> "complete", respond -> echo of the first tool summary (a
grounded answer), plan -> deterministic-looking empty plan with a clarification.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from app.agents.nodes.understand import NO_ACTION
from app.core.config import Settings

TOOL_BLOCK_RE = re.compile(r"<untrusted_tool_results>\n(.*?)\n</untrusted_tool_results>", re.S)
EVIDENCE_RE = re.compile(r"Evidence IDs available: \[(.*?)\]")


def _http_module(kind: str):
    """The HTTP library each SDK is built on (recent anthropic releases use `httpx2`, openai uses `httpx`)."""
    if kind == "anthropic":
        try:
            import anthropic._base_client as base

            if "httpx2" in getattr(base, "__dict__", {}) or hasattr(base, "httpx2"):
                import httpx2

                return httpx2
        except ImportError:  # pragma: no cover
            pass
    import httpx

    return httpx


def plan(intent: str, steps: list[tuple[str, dict]] | None = None, clarification: str = "",
         action: dict | None = None) -> dict:
    return {"intent": intent, "clarification": clarification, "action": {**NO_ACTION, **(action or {})},
            "steps": [{"tool": t, "args_json": json.dumps(a), "purpose": f"mock: {t}"} for t, a in steps or []]}


def review(complete: bool = True, steps: list[tuple[str, dict]] | None = None, missing: str = "") -> dict:
    return {"complete": complete, "missing": missing,
            "follow_up_steps": [{"tool": t, "args_json": json.dumps(a), "purpose": f"mock: {t}"} for t, a in steps or []]}


def tool_results(user_text: str) -> list[dict]:
    m = TOOL_BLOCK_RE.search(user_text)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def echo_answer(user_text: str) -> dict:
    """A grounded composer answer: restates the first tool summary verbatim and cites one real evidence id."""
    results = tool_results(user_text)
    summary = next((r.get("summary") for r in results if r.get("ok") and r.get("summary")), "No matching records.")
    m = EVIDENCE_RE.search(user_text)
    ids = [x.strip().strip("'\"") for x in m.group(1).split(",")] if m and m.group(1).strip() else []
    return {"answer": f"{summary}", "cited_evidence_ids": ids[:1]}


def _stage_from_schema(schema: dict) -> str:
    props = schema.get("properties", {})
    if "steps" in props:
        return "plan"
    if "complete" in props:
        return "review"
    return "respond"


class ScriptedBackend:
    def __init__(self, script: dict[str, list] | None, kind: str) -> None:
        self.queues = {k: list(v) for k, v in (script or {}).items()}
        self.kind = kind  # anthropic | openai
        self.httpx = _http_module(kind)
        self.requests: list[dict] = []  # every HTTP request body the SDK sent (incl. retries)

    def _next(self, stage: str, user: str) -> Any:
        q = self.queues.get(stage)
        if q:
            item = q.pop(0)
        elif stage == "review":
            item = review(True)
        elif stage == "respond":
            item = echo_answer
        else:
            item = plan("fleet_query", [], clarification="Which devices or metric do you mean?")
        return item(user) if callable(item) else item

    def handler(self, request):
        body = json.loads(request.content or b"{}")
        if self.kind == "anthropic":
            schema = body["output_config"]["format"]["schema"]
            user = body["messages"][0]["content"]
        else:
            schema = body["response_format"]["json_schema"]["schema"]
            user = next(m["content"] for m in body["messages"] if m["role"] == "user")
        stage = _stage_from_schema(schema)
        self.requests.append({"stage": stage, "body": body, "user": user})
        item = self._next(stage, user)
        if isinstance(item, tuple):
            if item[0] == "http":
                return self.httpx.Response(item[1], json={"type": "error", "error": {"type": "mock_error", "message": "mock"}},
                                      headers={"retry-after-ms": "5", "x-should-retry": "true" if item[1] in (429, 529, 503) else "false"})
            if item[0] == "timeout":
                raise self.httpx.ReadTimeout("mock timeout", request=request)
            if item[0] == "text":
                return self._ok(body, item[1], stop_reason=item[2])
            if item[0] == "refusal":
                return self._ok(body, "", stop_reason="refusal")
        return self._ok(body, json.dumps(item))

    def _ok(self, body: dict, text: str, stop_reason: str = "end_turn"):
        if self.kind == "anthropic":
            content = [{"type": "text", "text": text}] if text else []
            return self.httpx.Response(200, json={
                "id": "msg_mock", "type": "message", "role": "assistant", "model": body.get("model", "mock"),
                "content": content, "stop_reason": stop_reason, "stop_sequence": None,
                "usage": {"input_tokens": len(json.dumps(body)) // 4, "output_tokens": max(1, len(text) // 4)}})
        finish = {"end_turn": "stop", "max_tokens": "length", "refusal": "stop"}.get(stop_reason, "stop")
        message: dict = {"role": "assistant", "content": text or None}
        if stop_reason == "refusal":
            message["refusal"] = "I can't help with that."
        return self.httpx.Response(200, json={
            "id": "chatcmpl-mock", "object": "chat.completion", "created": 0, "model": body.get("model", "mock"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": len(json.dumps(body)) // 4, "completion_tokens": max(1, len(text) // 4),
                      "total_tokens": 0}})


def mock_provider(script: dict | None, kind: str = "anthropic", *, max_retries: int = 2,
                  timeout: float = 5.0) -> tuple[Any, ScriptedBackend]:
    """Real FleetPilot provider + real SDK client over a scripted HTTP transport."""
    from app.agents.llm.providers import AnthropicProvider, OpenAIProvider

    backend = ScriptedBackend(script, kind)
    hx = _http_module(kind)
    http = hx.Client(transport=hx.MockTransport(backend.handler))
    if kind == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key="mock-key-not-a-secret", base_url="http://mock-llm.local",
                                     http_client=http, max_retries=max_retries, timeout=timeout)
        settings = Settings(llm_provider="anthropic", llm_model="claude-opus-5-5")
        return AnthropicProvider(settings, client=client), backend
    import openai

    client = openai.OpenAI(api_key="mock-key-not-a-secret", base_url="http://mock-llm.local/v1", http_client=http,
                           max_retries=max_retries, timeout=timeout)
    settings = Settings(llm_provider="openai", llm_model="gpt-4o-mini")
    return OpenAIProvider(settings, client=client), backend


Picker = Callable[[str], dict]

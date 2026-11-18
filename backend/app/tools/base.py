"""Tool framework: typed inputs, defined outputs, tenant authorization, permission separation,
error handling, bounded retry and timing. The LLM can only *name* a tool and supply arguments;
validation and authorization happen here, outside the model's control."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.analytics.fleet import FleetAnalysis
from app.core.config import Settings, get_settings
from app.core.errors import FleetPilotError
from app.core.logging import get_logger
from app.repositories.telemetry import TelemetryRepository
from app.schemas.evidence import Evidence
from app.security.context import TenantContext

logger = get_logger(__name__)

# Arguments that identify the tenant are never accepted from a caller (LLM or user).
FORBIDDEN_ARGS = {"company_id", "tenant_id", "company", "org_id"}


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NoArgs(ToolInput):
    pass


@dataclass
class ToolContext:
    session: Session
    tenant: TenantContext
    settings: Settings = field(default_factory=get_settings)
    agent_run_id: str | None = None

    @cached_property
    def repo(self) -> TelemetryRepository:
        return TelemetryRepository(self.session, self.tenant)

    @cached_property
    def analysis(self) -> FleetAnalysis:
        return FleetAnalysis(self.repo)


class ToolOutput(BaseModel):
    """What a handler returns. `summary` is a deterministic, human-readable one-paragraph result."""

    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)


class ToolResult(BaseModel):
    tool: str
    args: dict[str, Any]
    ok: bool
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    duration_ms: int = 0
    attempts: int = 1


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]
    handler: Callable[[ToolContext, Any], ToolOutput]
    kind: Literal["read", "action"] = "read"
    permission: str = "telemetry:read"
    examples: tuple[str, ...] = ()

    def json_schema(self) -> dict:
        return self.input_model.model_json_schema()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self, kind: str | None = None) -> list[str]:
        return [n for n, s in self._tools.items() if kind is None or s.kind == kind]

    def catalog(self, kind: str | None = None) -> list[dict]:
        return [
            {"name": s.name, "kind": s.kind, "permission": s.permission, "description": s.description,
             "input_schema": s.json_schema()}
            for s in self._tools.values() if kind is None or s.kind == kind
        ]

    def execute(self, name: str, raw_args: dict | None, ctx: ToolContext, *, max_attempts: int = 2) -> ToolResult:
        started = time.perf_counter()
        args = dict(raw_args or {})
        spec = self._tools.get(name)

        def fail(code: str, msg: str, attempts: int = 1) -> ToolResult:
            return ToolResult(tool=name, args=args, ok=False, error=msg, error_code=code, attempts=attempts,
                              duration_ms=int((time.perf_counter() - started) * 1000))

        if spec is None:
            return fail("unknown_tool", f"Unknown tool {name!r}")
        smuggled = FORBIDDEN_ARGS & set(args)
        if smuggled:
            # Tenant comes only from the authenticated context; we never accept it as an argument.
            return fail("forbidden_argument",
                        f"Argument(s) {sorted(smuggled)} are not accepted; tenant is taken from your session")
        if not ctx.tenant.can(spec.permission):
            return fail("permission_denied", f"Role '{ctx.tenant.role}' lacks permission '{spec.permission}'")
        try:
            parsed = spec.input_model.model_validate(args)
        except ValidationError as exc:
            msg = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'args'}: {e['msg']}" for e in exc.errors()[:5])
            return fail("invalid_arguments", msg)

        attempt = 0
        while True:
            attempt += 1
            try:
                out = spec.handler(ctx, parsed)
                break
            except FleetPilotError as exc:
                return fail(exc.code, exc.message, attempt)
            except OperationalError as exc:  # transient DB failure -> bounded retry
                ctx.session.rollback()
                if attempt >= max_attempts:
                    logger.exception("tool db failure", extra={"ctx": {"tool": name}})
                    return fail("database_error", f"Database unavailable: {exc.__class__.__name__}", attempt)
                time.sleep(0.2 * attempt)
            except Exception as exc:  # noqa: BLE001 - tool failures must never crash the agent
                logger.exception("tool failure", extra={"ctx": {"tool": name}})
                return fail("tool_error", f"{exc.__class__.__name__}: {exc}", attempt)

        duration = int((time.perf_counter() - started) * 1000)
        if duration > ctx.settings.tool_timeout_seconds * 1000:
            return fail("timeout", f"Tool exceeded {ctx.settings.tool_timeout_seconds}s budget", attempt)
        # Evidence must belong to the caller's tenant — enforce, don't assume.
        foreign = [e.evidence_id for e in out.evidence if e.company_id != ctx.tenant.company_id]
        if foreign:
            return fail("tenant_violation", "Tool produced evidence outside the caller's tenant", attempt)
        return ToolResult(tool=name, args=parsed.model_dump(mode="json"), ok=True, summary=out.summary,
                          data=out.data, evidence=out.evidence, duration_ms=duration, attempts=attempt)


registry = ToolRegistry()


def tool(name: str, description: str, input_model: type[ToolInput] = NoArgs, *, kind: str = "read",
         permission: str = "telemetry:read", examples: tuple[str, ...] = ()):
    def deco(fn: Callable[[ToolContext, Any], ToolOutput]):
        registry.register(ToolSpec(name=name, description=description, input_model=input_model, handler=fn,
                                   kind=kind, permission=permission, examples=examples))  # type: ignore[arg-type]
        return fn
    return deco

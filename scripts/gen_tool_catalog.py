"""Regenerates docs/tool-catalog.md from the live tool registry (so the docs cannot drift)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.tools import registry  # noqa: E402


def fmt_args(schema: dict) -> list[str]:
    props = schema.get("properties", {})
    req = set(schema.get("required", []))
    rows = []
    for name, p in props.items():
        t = p.get("type") or " | ".join(x.get("type", "?") for x in p.get("anyOf", []) if x.get("type") != "null") or "?"
        if "enum" in p:
            t = " \\| ".join(map(str, p["enum"]))
        for x in p.get("anyOf", []):
            if "enum" in x:
                t = " \\| ".join(map(str, x["enum"]))
        rng = []
        for k, label in (("minimum", "≥"), ("exclusiveMinimum", ">"), ("maximum", "≤"), ("exclusiveMaximum", "<")):
            for src in [p, *p.get("anyOf", [])]:
                if k in src:
                    rng.append(f"{label}{src[k]}")
        default = p.get("default", "—" if name in req else None)
        desc = p.get("description", "")
        rows.append(f"| `{name}` | {t}{' (' + ', '.join(rng) + ')' if rng else ''} | {'yes' if name in req else 'no'} | "
                    f"{default if default is not None else 'null'} | {desc} |")
    return rows


def main() -> None:
    out = ["# Tool catalog", "",
           "_Generated from the live registry by `python scripts/gen_tool_catalog.py`._", "",
           "Every tool: Pydantic-validated arguments (`extra=forbid`), tenant taken from the authenticated session "
           "(any `company_id`-like argument is rejected), permission check, bounded retry on transient DB errors, "
           "timing, and a structured `ToolResult` = `{ok, summary, data, evidence[], error, error_code, duration_ms, attempts}`. "
           "Error codes: `invalid_arguments`, `forbidden_argument`, `permission_denied`, `not_found`, "
           "`insufficient_evidence`, `validation_failed`, `database_error`, `timeout`, `tool_error`, `tenant_violation`.", ""]
    for kind, title in (("read", "Read-only tools (permission `telemetry:read`)"),
                        ("action", "Action tools (permission `actions:propose`; create `pending_approval` proposals only)")):
        out += [f"## {title}", ""]
        for t in registry.catalog(kind):
            spec = registry.get(t["name"])
            out += [f"### `{t['name']}`", "", t["description"], ""]
            if spec.examples:
                out += ["Example requests: " + "; ".join(f"*{e}*" for e in spec.examples), ""]
            rows = fmt_args(t["input_schema"])
            if rows:
                out += ["| Argument | Type | Required | Default | Description |", "|---|---|---|---|---|", *rows, ""]
            else:
                out += ["No arguments.", ""]
    (ROOT / "docs" / "tool-catalog.md").write_text("\n".join(out), encoding="utf-8")
    print("wrote docs/tool-catalog.md")


if __name__ == "__main__":
    main()

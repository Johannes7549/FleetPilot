"""Prompt templates. Kept as plain text so they can be reviewed apart from business logic."""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent


def load(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")

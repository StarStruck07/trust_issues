"""Owner: Nilay (P2). STUB from Arth — see NILAY_data.md section 6.
Until the real one lands, every call raises LLMUnavailable so callers use their rule fallback."""
from __future__ import annotations

import os

from pydantic import BaseModel

from crd.data.errors import LLMUnavailable


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def complete_json(system: str, user: str, schema: type[BaseModel], run_id: str,
                  model: str | None = None, max_tokens: int = 800) -> BaseModel:
    raise LLMUnavailable("llm.py stub")

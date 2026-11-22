"""Config loader. Owner: Arth (P1). config.yaml is re-read on every get() so live edits apply next run."""
from __future__ import annotations

import os
from datetime import date
from typing import Any

import yaml

CONFIG_PATH = os.getenv("CRD_CONFIG", "config.yaml")


class Section(dict):
    """dict with attribute access, recursively."""

    def __getattr__(self, k: str) -> Any:
        try:
            v = self[k]
        except KeyError as e:
            raise AttributeError(k) from e
        return v

    def __setattr__(self, k: str, v: Any) -> None:
        self[k] = v


def _wrap(x: Any) -> Any:
    if isinstance(x, dict):
        return Section({k: _wrap(v) for k, v in x.items()})
    if isinstance(x, list):
        return [_wrap(v) for v in x]
    return x


Config = Section


def get(path: str | None = None) -> Config:
    p = path or os.getenv("CRD_CONFIG", CONFIG_PATH)
    with open(p, encoding="utf-8") as f:
        return _wrap(yaml.safe_load(f) or {})


def as_of(cfg: Config | None = None) -> date:
    cfg = cfg or get()
    v = os.getenv("CRD_AS_OF") or cfg.get("as_of")
    if v:
        return v if isinstance(v, date) else date.fromisoformat(str(v))
    return date.today()


def review_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}W{w:02d}"

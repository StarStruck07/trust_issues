"""Single switch point between Arth/Nilay's real modules and Prachi's stand-ins.

Every Prachi-owned module imports contracts from here:

    from crd.deps import models, db, config, orchestrator, explain, watcher

When ``crd/models.py`` + ``crd/db.py`` (Arth) and ``crd/data/watcher.py`` (Nilay)
land, they are picked up automatically. Force the stand-ins with ``CRD_USE_FAKES=1``.
A real module that exists but fails to import for another reason raises loudly;
we only fall back when the module file itself is missing.
"""
from __future__ import annotations

import importlib
import logging
import os

log = logging.getLogger("crd.deps")
_FORCE_FAKES = os.getenv("CRD_USE_FAKES") == "1"


def _load(real: str, fake: str):
    if not _FORCE_FAKES:
        try:
            return importlib.import_module(real)
        except ModuleNotFoundError as e:
            if not (e.name and (real == e.name or real.startswith(e.name + "."))):
                raise
    log.warning("using stand-in %s (real %s not found)", fake, real)
    return importlib.import_module(fake)


# models and db travel together: the fake db builds fake models.
try:
    if _FORCE_FAKES:
        raise ModuleNotFoundError(name="crd.models")
    models = importlib.import_module("crd.models")
    db = importlib.import_module("crd.db")
    USING_FAKE_DB = False
except ModuleNotFoundError as e:
    if e.name not in ("crd.models", "crd.db"):
        raise
    from crd.fakes import models, db  # noqa: F401
    USING_FAKE_DB = True
    log.warning("using stand-in crd.fakes.models / crd.fakes.db")

config = _load("crd.config", "crd.fakes.config")
orchestrator = _load("crd.orchestrator", "crd.fakes.orchestrator")
explain = _load("crd.explain", "crd.fakes.explain")
watcher = _load("crd.data.watcher", "crd.fakes.watcher")


def cfg_get(obj, key, default=None):
    """Read a config value whether config is a dict or an attribute object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

"""Owner: Prachi (P3). STUB from Arth — minimal JSON output until the real renderer lands (section 8)."""
from __future__ import annotations

import json
import os

from crd import db


def render(run_id: str) -> dict:
    out = f"var/runs/{run_id}"
    os.makedirs(out, exist_ok=True)
    path = f"{out}/review.json"
    data = {
        "run": db.get_run(run_id),
        "costs": db.run_costs(run_id),
        "assessments": [a.model_dump(mode="json") for a in db.get_assessments(run_id)],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return {"json": path}

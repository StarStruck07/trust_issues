#!/usr/bin/env bash
# Kill-on-stage rehearsal (Arth + Prachi). Starts a review, kills it mid-run with -9, resumes, shows outbox.
set -u
TEXT="${1:-Run this week's risk review on all our carriers and customers}"
INPUT="${2:-data/input/counterparties.csv}"
WAIT="${3:-8}"
python -m crd.cli init >/dev/null
python -m crd.cli review "$TEXT" --input "$INPUT" &
PID=$!
sleep "$WAIT"
echo ">>> kill -9 $PID"
kill -9 "$PID" 2>/dev/null
sleep 1
echo ">>> resume"
python -m crd.cli resume
echo ">>> actions"
python -m crd.cli actions
echo ">>> outbox rows (each idempotency key must appear once)"
python - <<'PY'
from crd import db
rows = db.list_outbox()
keys = [r["idempotency_key"] for r in rows]
print(f"{len(rows)} rows, {len(set(keys))} unique keys")
for r in rows: print(" ", r["sent_at"][:19], r["channel"], r["idempotency_key"])
PY

#!/usr/bin/env bash
# Stage demo (Prachi + Arth): approve an irreversible action in the web UI, kill -9 the server
# while it is "sending", restart, and show that the email went out exactly once.
#
#   terminal 1:  bash scripts/demo_kill_approval.sh
#   browser:     http://localhost:8000/approvals  -> type a name -> "Approve & send once"
#   terminal 1:  press Enter within 5 s of clicking approve  (kills the server with -9)
#   browser:     http://localhost:8000/outbox      -> one email, its idempotency key once
set -u
PORT="${PORT:-8000}"
SLOW="${CRD_SLOW_EXECUTE:-5}"

start() {
  CRD_SLOW_EXECUTE="$1" python -m uvicorn crd.web.app:app --port "$PORT" >var/demo_server.log 2>&1 &
  PID=$!
  for _ in $(seq 1 40); do curl -s "http://localhost:$PORT/health" >/dev/null && return; sleep 0.25; done
  echo "server did not start; see var/demo_server.log"; exit 1
}

mkdir -p var
echo ">>> starting server with CRD_SLOW_EXECUTE=$SLOW (the kill window)"
start "$SLOW"
echo ">>> open http://localhost:$PORT/approvals and approve an email, then press Enter here"
read -r _
echo ">>> kill -9 $PID  (mid-send)"
kill -9 "$PID" 2>/dev/null
sleep 1
python - <<'PY'
from crd import db
ex = db.get_actions("executing")
print(f">>> after the kill: {len(ex)} action(s) stuck in 'executing', {len(db.list_outbox())} outbox rows")
PY
echo ">>> restart: startup runs executor.reconcile() then execute_approved()"
start 0
sleep 2
python - <<'PY'
from crd import db
rows = db.list_outbox()
keys = [r["idempotency_key"] for r in rows]
print(f">>> outbox: {len(rows)} rows, {len(set(keys))} unique keys  ->  {'EXACTLY ONCE' if len(rows) == len(set(keys)) else 'DUPLICATE!'}")
for r in rows:
    print("   ", (r.get("sent_at") or "")[:19], r["channel"], r["idempotency_key"])
PY
echo ">>> server still running on :$PORT (pid $PID). Show /outbox, then: kill $PID"

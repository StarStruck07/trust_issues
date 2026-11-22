# Stage demo on Windows: approve in the UI, hard-kill the server mid-send, restart, show exactly one email.
#   powershell -ExecutionPolicy Bypass -File scripts\demo_kill_approval.ps1
param([int]$Port = 8000, [int]$Slow = 5)
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Force var | Out-Null

function Start-Desk([int]$slowSecs) {
    $env:CRD_SLOW_EXECUTE = "$slowSecs"
    $p = Start-Process python -ArgumentList "-m","uvicorn","crd.web.app:app","--port","$Port" -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput var\demo_server.out.log -RedirectStandardError var\demo_server.log
    for ($i = 0; $i -lt 40; $i++) {
        try { Invoke-RestMethod "http://localhost:$Port/health" | Out-Null; return $p } catch { Start-Sleep -Milliseconds 250 }
    }
    throw "server did not start; see var\demo_server.log"
}

Write-Host ">>> starting server with CRD_SLOW_EXECUTE=$Slow (the kill window)"
$server = Start-Desk $Slow
Write-Host ">>> open http://localhost:$Port/approvals, approve an email, then press Enter here within $Slow s"
[void](Read-Host)
Write-Host ">>> hard kill (Stop-Process -Force = kill -9) pid $($server.Id)"
Stop-Process -Id $server.Id -Force
Start-Sleep -Seconds 1
python -c "from crd import db; print('>>> after the kill:', len(db.get_actions('executing')), 'executing,', len(db.list_outbox()), 'outbox rows')"
Write-Host ">>> restart: startup runs executor.reconcile() then execute_approved()"
$server = Start-Desk 0
Start-Sleep -Seconds 2
python -c "from crd import db; r=db.list_outbox(); k=[x['idempotency_key'] for x in r]; print('>>> outbox:', len(r), 'rows,', len(set(k)), 'unique keys ->', 'EXACTLY ONCE' if len(r)==len(set(k)) else 'DUPLICATE!')"
Write-Host ">>> server running on :$Port (pid $($server.Id)). Show /outbox, then Stop-Process -Id $($server.Id)"

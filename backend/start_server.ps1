# AI Math Coach backend - self-healing launcher (Windows, local).
# ASCII-only on purpose: avoids GBK/UTF-8 corruption of Chinese paths on Win.
# All paths are derived from $PSScriptRoot so the folder can live anywhere.

$ErrorActionPreference = 'Continue'

$backendDir = $PSScriptRoot
$logDir     = Join-Path $backendDir 'logs'
$venv       = Join-Path $backendDir '.venv\Scripts\python.exe'

# ---- tunables -------------------------------------------------------------
$host_      = '127.0.0.1'
$port_      = 8000
$restartHours = 6          # planned restart to flush uvicorn memory
$crashDelay   = 3          # seconds to wait after a crash before restarting
# ---------------------------------------------------------------------------

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path $venv)) {
    Write-Host "[$(Get-Date)] FATAL: venv not found at $venv"
    Write-Host 'Run: python -m venv .venv  &&  .venv\Scripts\pip install -r requirements.txt'
    exit 1
}

Write-Host "[$(Get-Date)] self-healing loop start (planned restart every $restartHours h)"
Write-Host "[$(Get-Date)] logs -> $logDir"

while ($true) {
    $ts      = Get-Date -Format 'yyyyMMdd_HHmmss'
    $logFile = Join-Path $logDir ("server_$ts.log")
    $start   = Get-Date

    Write-Host "[$(Get-Date)] launching uvicorn (PID will print next) -> $logFile"
    $p = Start-Process -FilePath $venv `
        -ArgumentList @('-m','uvicorn','app.main:app',
                        '--host',$host_,'--port',[string]$port_,'--log-level','info') `
        -WorkingDirectory $backendDir `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError  $logFile `
        -PassThru -WindowStyle Hidden

    $pidUv = $p.Id
    Write-Host "[$(Get-Date)] uvicorn PID=$pidUv"

    # wait until uvicorn exits OR the planned-restart window elapses
    while (-not $p.HasExited) {
        Start-Sleep -Seconds 30
        if (((Get-Date) - $start).TotalHours -ge $restartHours) {
            Write-Host "[$(Get-Date)] reached $restartHours h -> planned restart to avoid memory dirt"
            Stop-Process -Id $pidUv -Force -ErrorAction SilentlyContinue
            break
        }
    }

    if ($p.HasExited) {
        $code = $p.ExitCode
        Write-Host "[$(Get-Date)] uvicorn exited (code=$code); restart in $crashDelay s"
    }
    else {
        Write-Host "[$(Get-Date)] uvicorn stopped by planned restart; restarting now"
    }
    Start-Sleep -Seconds $crashDelay
}

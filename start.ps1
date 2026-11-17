# start.ps1 - start everything FashionAI needs, each in its own window.
#
#   .\start.ps1            API (reachable from your phone) + cloud sync + the app's dev server
#   .\start.ps1 -NoCloud   same, without the Supabase sync
#   .\start.ps1 -NoApp     only the API (use this once the app is installed on the phone)
#
# Close a window (or press Ctrl+C in it) to stop that part.
param(
    [switch]$NoCloud,
    [switch]$NoApp
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root 'venv\Scripts\python.exe'
if (-not (Test-Path $python)) { Write-Host "Cannot find $python" -ForegroundColor Red; exit 1 }

# Ollama must be running for tagging and styling.
try { Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 3 | Out-Null }
catch { Write-Host 'Ollama is not running - start the Ollama app first, then run this again.' -ForegroundColor Yellow; exit 1 }

foreach ($port in 8000, 8081) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "Port $port is already in use - FashionAI (or something else) is running there. Stop it first." -ForegroundColor Yellow
        exit 1
    }
}

$serverArgs = @('server.py', '--lan')
if (-not $NoCloud -and (Test-Path (Join-Path $root '.supabase.env'))) { $serverArgs += '--cloud' }
Start-Process powershell -WorkingDirectory $root -ArgumentList '-NoExit', '-Command', "& '$python' $($serverArgs -join ' ')"

if (-not $NoApp) {
    Start-Process powershell -WorkingDirectory (Join-Path $root 'mobile') -ArgumentList '-NoExit', '-Command', 'npm start'
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.AddressState -eq 'Preferred' } |
    Select-Object -First 1).IPAddress

Write-Host ''
Write-Host 'FashionAI is starting (models take about 20 s to load).' -ForegroundColor Green
Write-Host "  API on this PC : http://127.0.0.1:8000/docs"
Write-Host "  API from phone : http://${ip}:8000        (direct mode only)"
if (-not $NoApp) {
    Write-Host "  App in browser : http://localhost:8081"
    Write-Host "  App in Expo Go : exp://${ip}:8081         (or scan the QR code in the other window)"
}
if (Test-Path (Join-Path $root '.api_key')) { Write-Host "  API key        : in $root\.api_key (direct mode only)" }
if ($serverArgs -contains '--cloud') { Write-Host '  Cloud sync     : on - the app''s cloud mode works from any network' }

<#
.SYNOPSIS
    Starts EchoAI: PostgreSQL, Ollama, the backend and the web UI.
.PARAMETER Https
    Serve HTTPS/WSS on port 8000 so other devices on your network can use the microphone.
.PARAMETER NoBrowser
    Do not open the browser.
#>
param([switch]$Https, [switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

if (-not (Test-Path $EnvFile) -or -not (Test-Path $Python)) {
    Write-Fail 'EchoAI is not set up yet. Run scripts\setup.ps1 first.'
    exit 1
}
New-Item -ItemType Directory -Force $RunDir, $LogDir | Out-Null

function Test-Running([string]$Name) {
    $file = Join-Path $RunDir "$Name.pid"
    if (-not (Test-Path $file)) { return $false }
    return [bool](Get-Process -Id ([int](Get-Content $file)) -ErrorAction SilentlyContinue)
}

function Start-Service-Process([string]$Name, [string[]]$Arguments) {
    if (Test-Running $Name) { Write-Ok "$Name is already running"; return }
    $process = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $Backend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir "$Name.out.log") -RedirectStandardError (Join-Path $LogDir "$Name.log")
    Set-Content (Join-Path $RunDir "$Name.pid") $process.Id
    Write-Ok "$Name started (pid $($process.Id))"
}

Write-Step 'PostgreSQL'
if (-not (Start-DockerEngine)) { exit 1 }
Invoke-Compose up -d --wait | Out-Null
Write-Ok 'Database is ready'

Write-Step 'Ollama'
if (-not (Start-OllamaServer)) { exit 1 }
Write-Ok 'Ollama is running'

Write-Step 'Database migrations'
$previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
Push-Location $Backend
& $Python -m alembic upgrade head 2>&1 | Out-Null
$migrated = $LASTEXITCODE
Pop-Location
$ErrorActionPreference = $previous
if ($migrated -ne 0) { Write-Fail 'Migrations failed.'; exit 1 }
Write-Ok 'Schema is current'

$ip = Get-LanIp
$backendArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000', '--log-level', 'info', '--no-access-log')
$scheme = 'http'
if ($Https) {
    Write-Step 'TLS certificate'
    $certDir = Join-Path $DataDir 'certs'
    & $Python (Join-Path $Backend 'scripts\generate_cert.py') --out $certDir --ip $ip
    # Relative to the backend folder, because the install path may contain spaces.
    $backendArgs += @('--ssl-keyfile', '../.data/certs/echoai.key', '--ssl-certfile', '../.data/certs/echoai.crt')
    $scheme = 'https'
}

Write-Step 'EchoAI services'
Start-Service-Process 'backend' $backendArgs
Start-Service-Process 'web' @('scripts/serve_web.py', '--port', '3000')

Write-Step 'Waiting for models to load'
$health = $null
$deadline = (Get-Date).AddSeconds(240)
while ((Get-Date) -lt $deadline) {
    # curl.exe (built into Windows 10+), because PowerShell 5.1 cannot reliably skip certificate
    # checks for our self-signed certificate. -k applies only to this request to our own machine.
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $raw = & curl.exe -sk --max-time 15 "${scheme}://127.0.0.1:8000/api/v1/health" 2>$null
    $ErrorActionPreference = $previous
    if ($raw) {
        try {
            $health = ($raw -join '') | ConvertFrom-Json
            if (-not $health.loading) { break }
        } catch { }
    }
    Start-Sleep -Seconds 2
}
if (-not $health) {
    Write-Fail "The backend did not respond. See $LogDir\backend.log"
    exit 1
}
foreach ($part in 'vad', 'stt', 'tts', 'llm') {
    $item = $health.models.$part
    if ($item.state -eq 'ready') { Write-Ok "$($part.ToUpper()): $($item.detail)" }
    else { Write-Warn "$($part.ToUpper()): $($item.state) $($item.detail)" }
}

$local = if ($Https) { 'https://localhost:8000' } else { 'http://localhost:3000' }
Write-Host ''
Write-Host "EchoAI is running." -ForegroundColor Green
Write-Host "  On this PC:      $local"
if ($Https) {
    Write-Host "  Other devices:   https://${ip}:8000  (accept the certificate warning once)"
} else {
    Write-Host "  Other devices:   http://${ip}:3000  (the microphone needs HTTPS there: use start.ps1 -Https)"
}
Write-Host '  Stop with:       scripts\stop.ps1'
if (-not $NoBrowser) { Start-Process $local }

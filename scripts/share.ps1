<#
.SYNOPSIS
    Makes this PC's EchoAI backend reachable from the hosted UI on Netlify.
.DESCRIPTION
    Starts a free Cloudflare quick tunnel to the backend on port 8000 and prints the link
    to open. The tunnel address changes every time this runs; the link carries it to the
    hosted UI, which remembers it. Run scripts\start.ps1 first. Stop with scripts\stop.ps1.
.PARAMETER Site
    The hosted UI.
#>
param(
    [string]$Site = 'https://echoai-ale55777.netlify.app',
    [string]$NetlifySiteId = '6c97b04e-3f38-4cc7-8b56-5bf0d20c66bb'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$cloudflared = Join-Path $DataDir 'bin\cloudflared.exe'
if (-not (Test-Path $cloudflared)) {
    Write-Step 'Downloading cloudflared'
    New-Item -ItemType Directory -Force (Split-Path $cloudflared) | Out-Null
    & curl.exe -sSL -o $cloudflared 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
    if ($LASTEXITCODE -ne 0) { Write-Fail 'Could not download cloudflared.'; exit 1 }
}

# The tunnel must reach whichever scheme start.ps1 used for the backend.
$previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$origin = $null
foreach ($candidate in 'http://127.0.0.1:8000', 'https://127.0.0.1:8000') {
    $code = & curl.exe -sk -o NUL -w '%{http_code}' --max-time 10 "$candidate/api/v1/health" 2>$null
    if ($code -eq '200') { $origin = $candidate; break }
}
$ErrorActionPreference = $previous
if (-not $origin) { Write-Fail 'The backend is not running. Run scripts\start.ps1 first.'; exit 1 }

$config = Read-EnvFile
if ($config['ECHOAI_CORS_ORIGINS'] -notmatch [regex]::Escape($Site)) {
    Write-Warn "ECHOAI_CORS_ORIGINS in .env does not include $Site, so the hosted UI will be refused."
}

New-Item -ItemType Directory -Force $RunDir, $LogDir | Out-Null
$pidFile = Join-Path $RunDir 'tunnel.pid'
if (Test-Path $pidFile) {
    $old = Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
    if ($old -and $old.Path -eq $cloudflared) { Stop-Process -Id $old.Id -Force }
    Remove-Item $pidFile -Force
}

Write-Step 'Starting Cloudflare tunnel'
$log = Join-Path $LogDir 'tunnel.log'
if (Test-Path $log) { Remove-Item $log -Force }
$tunnelArgs = @('tunnel', '--no-autoupdate', '--url', $origin, '--logfile', $log)
if ($origin -like 'https*') { $tunnelArgs += '--no-tls-verify' }  # Our own self-signed certificate.
$process = Start-Process -FilePath $cloudflared -ArgumentList $tunnelArgs -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $LogDir 'tunnel.out.log') -RedirectStandardError (Join-Path $LogDir 'tunnel.err.log')
Set-Content $pidFile $process.Id

$url = $null
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline -and -not $url) {
    Start-Sleep -Seconds 1
    if ($process.HasExited) { break }
    foreach ($file in $log, (Join-Path $LogDir 'tunnel.err.log')) {
        if (Test-Path $file) {
            $match = Select-String -Path $file -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($match) { $url = $match.Matches[0].Value; break }
        }
    }
}
if (-not $url) { Write-Fail "The tunnel did not start. See $log"; exit 1 }

# A new tunnel hostname takes a few seconds to resolve worldwide.
$previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
    $code = & curl.exe -s -o NUL -w '%{http_code}' --max-time 10 "$url/api/v1/health" 2>$null
    if ($code -eq '200') { break }
    Start-Sleep -Seconds 2
}
$ErrorActionPreference = $previous

Write-Ok "Tunnel is up: $url"

# Point the hosted UI at this tunnel, so the plain site address always works. Only backend.js
# differs from the local build, and Netlify uploads changed files only, so this is quick.
$link = "$Site/?api=$url"
$built = Join-Path $Root 'apps\web\out'
if ($NetlifySiteId -and (Test-Path $built) -and (Get-Command netlify.cmd -ErrorAction SilentlyContinue)) {
    Write-Step 'Publishing the tunnel address to Netlify'
    $staging = Join-Path $RunDir 'netlify-site'
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
    Copy-Item $built $staging -Recurse
    Set-Content (Join-Path $staging 'backend.js') "window.__ECHOAI_API__ = `"$url`";" -Encoding ascii
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & netlify.cmd deploy --prod --no-build --dir $staging --site $NetlifySiteId --message 'Tunnel address' 2>&1 | Out-Null
    $deployed = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($deployed -eq 0) { $link = $Site; Write-Ok 'Netlify now points at this PC' }
    else { Write-Warn 'Could not update Netlify; use the longer link below.' }
}

Write-Host ''
Write-Host 'Open Jarvis from any device:' -ForegroundColor Green
Write-Host "  $link"
Write-Host '  Works while this PC is on, awake and running Jarvis.'
Write-Host '  Stop with: scripts\stop.ps1'
Set-Content (Join-Path $RunDir 'share-link.txt') $link

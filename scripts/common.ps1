# Helpers shared by setup.ps1, start.ps1 and stop.ps1.
$script:Root = Split-Path -Parent $PSScriptRoot
$script:Backend = Join-Path $Root 'backend'
$script:WebDir = Join-Path $Root 'apps\web'
$script:EnvFile = Join-Path $Root '.env'
$script:DataDir = Join-Path $Root '.data'
$script:RunDir = Join-Path $DataDir 'run'
$script:LogDir = Join-Path $DataDir 'logs'
$script:Python = Join-Path $Backend '.venv\Scripts\python.exe'
$script:Compose = Join-Path $Root 'infrastructure\docker-compose.yml'

function Write-Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [ok] $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "  [!] $Text" -ForegroundColor Yellow }
function Write-Fail([string]$Text) { Write-Host "  [x] $Text" -ForegroundColor Red }

function Read-EnvFile {
    $values = @{}
    if (Test-Path $EnvFile) {
        foreach ($line in Get-Content $EnvFile) {
            if ($line -match '^\s*([A-Z0-9_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
        }
    }
    return $values
}

function Set-EnvValue([string]$Name, [string]$Value) {
    $lines = @()
    if (Test-Path $EnvFile) { $lines = @(Get-Content $EnvFile) }
    $found = $false
    $lines = @($lines | ForEach-Object {
        if ($_ -match "^$Name=") { $found = $true; "$Name=$Value" } else { $_ }
    })
    if (-not $found) { $lines += "$Name=$Value" }
    [System.IO.File]::WriteAllLines($EnvFile, [string[]]$lines)
}

function Get-LanIp {
    $config = Read-EnvFile
    if ($config['ECHOAI_LAN_IP']) { return $config['ECHOAI_LAN_IP'] }
    # The interface holding the default route is the real network, not a virtual switch.
    $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric | Select-Object -First 1
    if ($route) {
        $ip = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($ip) { return $ip.IPAddress }
    }
    return '127.0.0.1'
}

function Find-Ollama {
    $config = Read-EnvFile
    $candidates = @($config['ECHOAI_OLLAMA_EXE'], (Get-Command ollama -ErrorAction SilentlyContinue).Source,
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Find-DockerDesktop {
    $docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
    $candidates = @("$env:ProgramFiles\Docker\Docker\Docker Desktop.exe")
    if ($docker) { $candidates += (Join-Path (Split-Path (Split-Path (Split-Path $docker))) 'Docker Desktop.exe') }
    foreach ($candidate in $candidates) { if (Test-Path $candidate) { return $candidate } }
    return $null
}

function Test-DockerRunning {
    # With a stopped daemon docker writes to stderr, which is fatal under 'Stop' in PowerShell 5.1.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Start-DockerEngine([int]$TimeoutSeconds = 180) {
    if (Test-DockerRunning) { return $true }
    $desktop = Find-DockerDesktop
    if (-not $desktop) { Write-Fail 'Docker Desktop was not found.'; return $false }
    Write-Host '  Starting Docker Desktop (this can take a minute)...'
    Start-Process $desktop | Out-Null
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerRunning) { return $true }
        Start-Sleep -Seconds 3
    }
    Write-Fail 'Docker did not become ready in time.'
    return $false
}

function Test-Ollama {
    $url = (Read-EnvFile)['ECHOAI_OLLAMA_URL']
    if (-not $url) { $url = 'http://127.0.0.1:11434' }
    try { Invoke-RestMethod "$url/api/version" -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

function Start-OllamaServer {
    if (Test-Ollama) { return $true }
    $exe = Find-Ollama
    if (-not $exe) { Write-Fail 'Ollama was not found.'; return $false }
    Start-Process $exe -ArgumentList 'serve' -WindowStyle Hidden | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-Ollama) { return $true }
        Start-Sleep -Seconds 1
    }
    Write-Fail 'Ollama did not start.'
    return $false
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    # Docker prints progress on stderr, which PowerShell 5.1 would report as an error.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker compose --progress quiet --env-file $EnvFile -f $Compose @Arguments 2>&1 | ForEach-Object { "$_" }
        return
    } finally {
        $ErrorActionPreference = $previous
    }
}

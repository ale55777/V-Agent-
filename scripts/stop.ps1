<#
.SYNOPSIS
    Stops EchoAI's own processes and its database container.
.DESCRIPTION
    Saved data is kept. Docker Desktop and Ollama are left running, because other apps
    may use them; pass -All to also stop the Ollama server this project uses.
#>
param([switch]$All)

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'common.ps1')

Write-Step 'Stopping EchoAI'
foreach ($name in 'backend', 'web') {
    $file = Join-Path $RunDir "$name.pid"
    if (Test-Path $file) {
        $processId = [int](Get-Content $file)
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        # Pid files can go stale after a reboot, so check it really is our Python first.
        if ($process -and $process.Path -eq $Python) {
            # A venv's python.exe is a launcher with the real interpreter as its child,
            # so the whole process tree has to go.
            & taskkill.exe /T /F /PID $processId 2>&1 | Out-Null
            Write-Ok "Stopped $name"
        }
        Remove-Item $file -Force
    }
}

$tunnelPid = Join-Path $RunDir 'tunnel.pid'
if (Test-Path $tunnelPid) {
    $process = Get-Process -Id ([int](Get-Content $tunnelPid)) -ErrorAction SilentlyContinue
    if ($process -and $process.Path -eq (Join-Path $DataDir 'bin\cloudflared.exe')) {
        Stop-Process -Id $process.Id -Force
        Write-Ok 'Stopped the Cloudflare tunnel'
    }
    Remove-Item $tunnelPid -Force
}

if (Test-DockerRunning) {
    Invoke-Compose stop | Out-Null
    Write-Ok 'Stopped PostgreSQL (data is kept)'
}

# The backend keeps the model loaded for fast replies, so hand that memory back.
$config = Read-EnvFile
if ((Test-Ollama) -and $config['ECHOAI_LLM_MODEL']) {
    $url = if ($config['ECHOAI_OLLAMA_URL']) { $config['ECHOAI_OLLAMA_URL'] } else { 'http://127.0.0.1:11434' }
    try {
        $body = @{ model = $config['ECHOAI_LLM_MODEL']; keep_alive = 0 } | ConvertTo-Json
        Invoke-RestMethod "$url/api/generate" -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 15 | Out-Null
        Write-Ok 'Unloaded the AI model from memory'
    } catch {
        Write-Warn 'Could not unload the AI model.'
    }
}

if ($All) {
    Get-Process -Name 'ollama' -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Ok 'Stopped Ollama'
}
Write-Host 'Done.' -ForegroundColor Green

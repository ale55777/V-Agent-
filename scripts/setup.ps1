<#
.SYNOPSIS
    Installs and verifies everything EchoAI needs on Windows. Safe to run again.
.PARAMETER Yes
    Approve prerequisite installs without asking.
.PARAMETER InstallDir
    Where to install Ollama if it is missing, and keep its models. Useful when C: is nearly full.
.PARAMETER LlmModel
    Override the automatically selected Qwen model.
#>
param([switch]$Yes, [string]$InstallDir, [string]$LlmModel, [string]$SttModel)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$summary = New-Object System.Collections.Generic.List[string]
function Add-Summary([string]$Text) { $summary.Add($Text) | Out-Null }

function Confirm-Action([string]$Question) {
    if ($Yes) { return $true }
    return ((Read-Host "$Question [y/N]") -match '^(y|yes)$')
}

function Invoke-Retry([scriptblock]$Action, [string]$Label, [int]$Attempts = 3) {
    for ($i = 1; $i -le $Attempts; $i++) {
        & $Action
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warn "$Label failed (attempt $i of $Attempts)"
        if ($i -lt $Attempts) { Start-Sleep -Seconds (5 * $i) }
    }
    throw "$Label failed after $Attempts attempts."
}

function Install-WithWinget([string]$Id, [string]$Label, [string[]]$Extra = @()) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$Label is missing and winget is unavailable. Install $Label manually, then run this again."
    }
    if (-not (Confirm-Action "$Label is missing. Install it from the official winget source ($Id)?")) {
        throw "$Label is required. Install it, then run this again."
    }
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & winget install --id $Id --exact --accept-source-agreements --accept-package-agreements @Extra
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code -ne 0) { throw "winget could not install $Label (exit code $code)." }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

try {
    New-Item -ItemType Directory -Force $DataDir, $RunDir, $LogDir | Out-Null

    # ---- Machine --------------------------------------------------------------------
    Write-Step 'Detecting hardware'
    $os = Get-CimInstance Win32_OperatingSystem
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $ramGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    $freeRamGb = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
    Write-Ok "Windows: $($os.Caption) ($($os.Version))"
    Write-Ok "CPU: $($cpu.Name.Trim()), $($cpu.NumberOfCores) cores"
    Write-Ok "RAM: $ramGb GB total, $freeRamGb GB free"

    $vramGb = 0
    $gpuName = (Get-CimInstance Win32_VideoController | Where-Object { $_.Name -notmatch 'Idd|Basic|Virtual' } | Select-Object -First 1).Name
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $line = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits | Select-Object -First 1
        $parts = $line -split ','
        $gpuName = $parts[0].Trim()
        $vramGb = [math]::Round([double]$parts[1] / 1024, 1)
    }
    $vramText = if ($vramGb) { "$vramGb GB VRAM" } else { 'no usable compute VRAM' }
    Write-Ok "GPU: $gpuName ($vramText)"
    Add-Summary "Hardware: $($cpu.Name.Trim()), $ramGb GB RAM, $gpuName ($vramText)"

    foreach ($disk in Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3') {
        Write-Host ("  Disk {0} {1:N1} GB free" -f $disk.DeviceID, ($disk.FreeSpace / 1GB))
    }
    $projectDrive = (Get-Item $Root).PSDrive.Name
    $freeGb = (Get-PSDrive $projectDrive).Free / 1GB
    if ($freeGb -lt 4) { throw "Drive ${projectDrive}: has only $([math]::Round($freeGb, 1)) GB free; EchoAI needs about 4 GB." }

    # Everything below stays out of a full system drive where possible.
    $cache = Join-Path (Split-Path $Root -Qualifier) '\.echoai-cache'
    New-Item -ItemType Directory -Force (Join-Path $cache 'temp') | Out-Null
    $env:TEMP = Join-Path $cache 'temp'
    $env:TMP = $env:TEMP
    $env:PIP_CACHE_DIR = Join-Path $cache 'pip'
    $env:npm_config_cache = Join-Path $cache 'npm'
    $env:NEXT_TELEMETRY_DISABLED = '1'

    # ---- Model selection ------------------------------------------------------------
    if (-not $LlmModel) {
        if ($vramGb -ge 8) { $LlmModel = 'qwen2.5:3b-instruct' }
        elseif ($vramGb -ge 4 -or $ramGb -ge 16) { $LlmModel = 'qwen2.5:1.5b-instruct' }
        else { $LlmModel = 'qwen2.5:0.5b-instruct' }
    }
    if (-not $SttModel) {
        if ($vramGb -ge 8 -or $ramGb -ge 32) { $SttModel = 'small' }
        elseif ($ramGb -ge 16) { $SttModel = 'base' }
        else { $SttModel = 'tiny' }
    }
    Write-Ok "Selected models: $LlmModel, faster-whisper $SttModel"
    Add-Summary "Models: $LlmModel, faster-whisper $SttModel"

    # ---- Prerequisites --------------------------------------------------------------
    Write-Step 'Checking prerequisites'
    $pythonOk = $false
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $version = (& python --version 2>&1).ToString()
        if ($version -match 'Python 3\.(1[2-9]|[2-9]\d)') { $pythonOk = $true; Write-Ok $version }
    }
    if (-not $pythonOk) { Install-WithWinget 'Python.Python.3.12' 'Python 3.12' }

    if (Get-Command node -ErrorAction SilentlyContinue) { Write-Ok "Node.js $(& node --version)" }
    else { Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js' }

    if (Get-Command docker -ErrorAction SilentlyContinue) { Write-Ok 'Docker is installed' }
    else {
        Install-WithWinget 'Docker.DockerDesktop' 'Docker Desktop'
        Write-Warn 'Docker Desktop may require a restart. If later steps fail, restart Windows and run this again.'
    }

    $ollama = Find-Ollama
    if ($ollama) { Write-Ok "Ollama: $ollama" }
    else {
        $extra = @()
        if ($InstallDir) { $extra = @('--location', (Join-Path $InstallDir 'Ollama')) }
        Install-WithWinget 'Ollama.Ollama' 'Ollama' $extra
        $ollama = Find-Ollama
        if (-not $ollama) { throw 'Ollama was installed but could not be found. Open a new terminal and run this again.' }
    }
    if ($InstallDir) {
        $modelsDir = Join-Path $InstallDir 'Ollama\models'
        if (Confirm-Action "Store Ollama models in $modelsDir (sets a user environment variable)?") {
            [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $modelsDir, 'User')
            $env:OLLAMA_MODELS = $modelsDir
        }
    }

    # ---- Configuration --------------------------------------------------------------
    Write-Step 'Writing configuration'
    function New-Secret([int]$Bytes) {
        $data = New-Object byte[] $Bytes
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($data)
        return ([Convert]::ToBase64String($data) -replace '[+/=]', '')
    }
    if (-not (Test-Path $EnvFile)) {
        $password = New-Secret 24
        @(
            "ECHOAI_SECRET_KEY=$(New-Secret 48)",
            'POSTGRES_USER=echoai',
            "POSTGRES_PASSWORD=$password",
            'POSTGRES_DB=echoai',
            'POSTGRES_PORT=5432',
            "ECHOAI_DATABASE_URL=postgresql+asyncpg://echoai:$password@127.0.0.1:5432/echoai",
            'ECHOAI_HOST=0.0.0.0',
            'ECHOAI_PORT=8000',
            'ECHOAI_OLLAMA_URL=http://127.0.0.1:11434',
            'ECHOAI_TTS_ENGINE=auto',
            'ECHOAI_POWER_WATTS=25'
        ) | Set-Content -Encoding Ascii $EnvFile
        Write-Ok 'Created .env with new random secrets'
    } else {
        Write-Ok 'Keeping the existing .env'
    }
    Set-EnvValue 'ECHOAI_LLM_MODEL' $LlmModel
    Set-EnvValue 'ECHOAI_STT_MODEL' $SttModel
    Set-EnvValue 'ECHOAI_OLLAMA_EXE' $ollama
    $ip = Get-LanIp
    Set-EnvValue 'ECHOAI_LAN_IP' $ip
    Write-Ok "LAN address: $ip (edit ECHOAI_LAN_IP in .env to override)"

    # ---- Python ---------------------------------------------------------------------
    Write-Step 'Installing backend packages'
    $ErrorActionPreference = 'Continue'
    if (-not (Test-Path $Python)) { & python -m venv (Join-Path $Backend '.venv') }
    Invoke-Retry { & $Python -m pip install --upgrade pip --quiet --disable-pip-version-check } 'pip upgrade'
    Invoke-Retry { & $Python -m pip install -r (Join-Path $Backend 'requirements-dev.txt') --disable-pip-version-check } 'Backend packages'
    Write-Ok 'Backend packages installed'

    # ---- Frontend -------------------------------------------------------------------
    Write-Step 'Installing and building the web app'
    Push-Location $Root
    # npm.cmd, because npm's PowerShell shim misparses its arguments inside a scriptblock.
    Invoke-Retry { & npm.cmd install --no-audit --no-fund } 'npm install'
    & npm.cmd run build:web
    if ($LASTEXITCODE -ne 0) { throw 'The web build failed.' }
    Pop-Location
    Write-Ok 'Web app built'

    # ---- Models ---------------------------------------------------------------------
    Write-Step "Downloading the AI model ($LlmModel)"
    if (-not (Start-OllamaServer)) { throw 'Could not start Ollama.' }
    Invoke-Retry { & $ollama pull $LlmModel } "Pull $LlmModel"
    Write-Ok 'Ollama verified the model digest during the pull'

    Write-Step 'Downloading speech models (Silero VAD, Kokoro, Piper, Whisper)'
    Invoke-Retry { & $Python (Join-Path $Backend 'scripts\download_models.py') --stt $SttModel } 'Model download'

    # ---- Database -------------------------------------------------------------------
    Write-Step 'Starting PostgreSQL'
    if (-not (Start-DockerEngine)) { throw 'Docker is not running.' }
    Invoke-Retry { Invoke-Compose up -d --wait | Out-Null } 'docker compose up'
    Push-Location $Backend
    Invoke-Retry { & $Python -m alembic upgrade head } 'Database migration'
    Pop-Location
    Write-Ok 'Database is ready and migrated'

    # ---- Verification ---------------------------------------------------------------
    Write-Step 'Verifying the models on this machine'
    $report = Join-Path $DataDir 'benchmark.json'
    & $Python (Join-Path $Backend 'scripts\verify_models.py') --output $report
    if ($LASTEXITCODE -ne 0) { throw 'Model verification failed. See the output above.' }
    Add-Summary "Benchmark saved: $report"

    Write-Step 'Testing the backend'
    Push-Location $Backend
    & $Python -m pytest tests -q --timeout=60 -p no:logging
    $testCode = $LASTEXITCODE
    Pop-Location
    if ($testCode -ne 0) { Write-Warn 'Some automated tests failed. See the output above.'; Add-Summary 'Tests: FAILURES, see setup output' }
    else { Add-Summary 'Tests: all passed' }

    & (Join-Path $PSScriptRoot 'start.ps1') -NoBrowser
    $health = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 15
    Add-Summary "Health: $($health.status)"
    & (Join-Path $PSScriptRoot 'stop.ps1')

    Write-Step 'Setup summary'
    $summaryFile = Join-Path $DataDir 'setup-summary.txt'
    $summary | Set-Content $summaryFile
    $summary | ForEach-Object { Write-Host "  $_" }
    Write-Host "`nSetup finished. Start EchoAI with: scripts\start.ps1" -ForegroundColor Green
}
catch {
    Write-Host ''
    Write-Fail $_.Exception.Message
    Write-Host 'Fix the problem above and run scripts\setup.ps1 again. Finished steps are skipped.'
    exit 1
}

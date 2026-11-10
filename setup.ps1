#Requires -Version 5.1
# ==============================================================================
#  YouTube Shorts Automation Engine - COMPREHENSIVE SETUP SCRIPT
#  Version: 2.0  |  Requires: PowerShell 5.1+  |  Platform: Windows
# ==============================================================================
# Usage:
#   .\setup.ps1                  # Standard setup
#   .\setup.ps1 -GPU             # Force CUDA torch reinstall for NVIDIA GPU
#   .\setup.ps1 -SkipTests       # Skip final import verification
#   .\setup.ps1 -NoBrowser       # Don't auto-open browser at end
#   .\setup.ps1 -NoServer        # Skip server start test
# ==============================================================================

[CmdletBinding()]
param(
    [switch]$GPU,          # Force CUDA-enabled PyTorch install
    [switch]$SkipTests,    # Skip final module import verification
    [switch]$NoBrowser,    # Don't attempt to open browser at completion
    [switch]$NoServer      # Skip final server smoke-test
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
#  CONSTANTS & PATH SETUP
# -----------------------------------------------------------------------------
$SCRIPT_VERSION    = "2.0"
$MIN_PYTHON_MAJOR  = 3
$MIN_PYTHON_MINOR  = 11
$PROJECT_ROOT      = $PSScriptRoot   # Resolves correctly even with spaces in path
$VENV_DIR          = Join-Path $PROJECT_ROOT ".venv"
$VENV_PYTHON       = Join-Path $VENV_DIR "Scripts\python.exe"
$VENV_PIP          = Join-Path $VENV_DIR "Scripts\pip.exe"
$VENV_ACTIVATE     = Join-Path $VENV_DIR "Scripts\Activate.ps1"
$REQ_FILE          = Join-Path $PROJECT_ROOT "requirements.txt"
$ENV_FILE          = Join-Path $PROJECT_ROOT ".env"
$ENV_EXAMPLE       = Join-Path $PROJECT_ROOT ".env.example"
$PYPROJECT         = Join-Path $PROJECT_ROOT "pyproject.toml"
$SETUP_ENV_PY      = Join-Path $PROJECT_ROOT "setup_environment.py"
$LOG_DIR           = Join-Path $PROJECT_ROOT "storage\logs"
$LOG_FILE          = Join-Path $LOG_DIR "setup.log"
$TOOLS_FFMPEG_BIN  = Join-Path $PROJECT_ROOT "tools\ffmpeg\bin"
$RUNTIME_CFG       = Join-Path $PROJECT_ROOT "storage\config\runtime_settings.json"
$TOTAL_STEPS       = 10

# Tracking
$script:Warnings   = @()
$script:Errors     = @()
$script:PythonExe  = $null
$script:StepNum    = 0

# -----------------------------------------------------------------------------
#  LOGGING & OUTPUT HELPERS
# -----------------------------------------------------------------------------
function Initialize-Logging {
    if (-not (Test-Path $LOG_DIR)) {
        New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null
    }
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $header = @"
================================================================================
  YouTube Shorts Automation Engine - Setup Log
  Script Version : $SCRIPT_VERSION
  Started        : $timestamp
  Project Root   : $PROJECT_ROOT
  Parameters     : GPU=$GPU  SkipTests=$SkipTests  NoBrowser=$NoBrowser  NoServer=$NoServer
================================================================================
"@
    $header | Out-File -FilePath $LOG_FILE -Encoding UTF8 -Force
}

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "HH:mm:ss"
    "[$ts] $Message" | Out-File -FilePath $LOG_FILE -Encoding UTF8 -Append
}

function Write-Step {
    param([string]$Title)
    $script:StepNum++
    $line = "`n[$($script:StepNum)/$TOTAL_STEPS] $Title"
    Write-Host $line -ForegroundColor Cyan
    Write-Host ("-" * 60) -ForegroundColor DarkGray
    Write-Log $line
}

function Write-OK    { param([string]$M) Write-Host "  [OK] $M"      -ForegroundColor Green;  Write-Log "[OK]   $M" }
function Write-Warn  { param([string]$M) Write-Host "  [WARN] $M"    -ForegroundColor Yellow; Write-Log "[WARN] $M"; $script:Warnings += $M }
function Write-Err   { param([string]$M) Write-Host "  [ERR] $M"     -ForegroundColor Red;    Write-Log "[ERR]  $M"; $script:Errors   += $M }
function Write-Info  { param([string]$M) Write-Host "  $M"           -ForegroundColor Gray;   Write-Log "       $M" }
function Write-Fatal {
    param([string]$M, [string]$Hint = "")
    Write-Host "`n[FATAL] $M" -ForegroundColor Red
    if ($Hint) { Write-Host "  HINT: $Hint" -ForegroundColor Yellow }
    Write-Log "[FATAL] $M"
    if ($Hint) { Write-Log "  HINT: $Hint" }
    Write-Host "`nSetup cannot continue. Check log: $LOG_FILE" -ForegroundColor Red
    Read-Host "Press ENTER to exit"
    exit 1
}

# -----------------------------------------------------------------------------
#  BANNER
# -----------------------------------------------------------------------------
function Show-Banner {
    Clear-Host
    Write-Host ""
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host "      YouTube Shorts Automation Engine  -  Complete Setup  (v$SCRIPT_VERSION)" -ForegroundColor Cyan
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host "  Project  : $PROJECT_ROOT" -ForegroundColor Gray
    Write-Host "  Log file : $LOG_FILE" -ForegroundColor Gray
    if ($GPU)       { Write-Host "  Mode     : CUDA/GPU forced" -ForegroundColor Yellow }
    if ($SkipTests) { Write-Host "  Tests    : Skipped" -ForegroundColor Yellow }
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host ""
}

# -----------------------------------------------------------------------------
#  STEP 1 - PYTHON DETECTION & AUTO-INSTALL
# -----------------------------------------------------------------------------
function Find-ValidPython {
    <#  Returns path to a python.exe that is >= 3.11, or $null if none found. #>
    $candidates = @()

    # Candidates from PATH
    foreach ($cmd in @('python', 'python3', 'py')) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) { $candidates += $found.Source }
    }

    # Common Windows install locations
    $localApp = [System.Environment]::GetFolderPath('LocalApplicationData')
    $programFiles = ${env:ProgramFiles}
    foreach ($ver in @('Python313','Python312','Python311','Python310')) {
        $candidates += @(
            "$localApp\Programs\Python\$ver\python.exe",
            "$programFiles\Python\$ver\python.exe",
            "C:\Python$($ver.Replace('Python',''))\python.exe"
        )
    }
    # py launcher embedded versions
    foreach ($v in @('3.13','3.12','3.11','3.10')) {
        $candidates += "py -$v"
    }

    foreach ($cand in ($candidates | Select-Object -Unique)) {
        try {
            if ($cand -like 'py *') {
                $arg = $cand.Split(' ')[1]
                $ver = & py $arg --version 2>&1
            } else {
                if (-not (Test-Path $cand -ErrorAction SilentlyContinue)) { continue }
                $ver = & "$cand" --version 2>&1
            }
            if ($ver -match 'Python (\d+)\.(\d+)') {
                $maj = [int]$Matches[1]; $min = [int]$Matches[2]
                if ($maj -gt $MIN_PYTHON_MAJOR -or ($maj -eq $MIN_PYTHON_MAJOR -and $min -ge $MIN_PYTHON_MINOR)) {
                    if ($cand -like 'py *') {
                        # Resolve actual exe path via py launcher
                        $actualPath = & py $cand.Split(' ')[1] -c "import sys; print(sys.executable)" 2>&1
                        return $actualPath.Trim()
                    }
                    return $cand
                }
            }
        } catch { <# skip bad candidates #> }
    }
    return $null
}

function Refresh-PathFromRegistry {
    Write-Info "Refreshing PATH from Windows Registry..."
    $machinePath = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath    = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machinePath;$userPath"
}

function Step-Python {
    Write-Step "Python Detection & Validation"

    $script:PythonExe = Find-ValidPython
    if ($script:PythonExe) {
        $verStr = & "$($script:PythonExe)" --version 2>&1
        Write-OK "Found: $($script:PythonExe)  ($verStr)"
        return
    }

    Write-Warn "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ not found.  Attempting auto-install via winget..."

    # Check winget availability
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Fatal "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ not found and winget is unavailable." `
            "Install Python 3.11 manually from: https://www.python.org/downloads/  (check 'Add Python to PATH')"
    }

    try {
        Write-Info "Running: winget install Python.Python.3.11 --silent --accept-source-agreements --accept-package-agreements"
        & winget install Python.Python.3.11 --silent --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
            # -1978335189 = APPINSTALLER_ERROR_ALREADY_INSTALLED
            throw "winget exited with code $LASTEXITCODE"
        }
    } catch {
        Write-Fatal "winget install failed: $_" `
            "Download Python 3.11 from https://www.python.org/downloads/ and re-run this script."
    }

    Refresh-PathFromRegistry
    $script:PythonExe = Find-ValidPython

    if (-not $script:PythonExe) {
        Write-Fatal "Python was installed but still not detected after PATH refresh." `
            "Close this terminal, open a new one, and re-run setup.ps1"
    }

    $verStr = & "$($script:PythonExe)" --version 2>&1
    Write-OK "Python installed and detected: $($script:PythonExe)  ($verStr)"
}

# -----------------------------------------------------------------------------
#  STEP 2 - FFMPEG DETECTION & AUTO-DOWNLOAD
# -----------------------------------------------------------------------------
function Step-FFmpeg {
    Write-Step "FFmpeg Detection & Setup"

    # 1. Already in PATH?
    $ffInPath = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($ffInPath) {
        $v = & ffmpeg -version 2>&1 | Select-String 'ffmpeg version' | Select-Object -First 1
        Write-OK "ffmpeg in PATH: $($ffInPath.Source)  ($v)"
        return
    }

    # 2. Project-local bundled?
    $localExe = Join-Path $TOOLS_FFMPEG_BIN "ffmpeg.exe"
    if (Test-Path $localExe) {
        $env:Path = "$TOOLS_FFMPEG_BIN;$env:Path"
        Write-OK "Project-local ffmpeg found at: $localExe  (added to session PATH)"
        return
    }

    # 3. Auto-download BtbN GPL build
    Write-Info "FFmpeg not found - attempting auto-download (BtbN GPL build)..."
    $downloadUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    $zipPath     = Join-Path $env:TEMP "ffmpeg_download.zip"
    $extractPath = Join-Path $env:TEMP "ffmpeg_extract"

    try {
        Write-Info "Downloading from: $downloadUrl"
        Write-Info "This may take a minute (approx 80 MB)..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $webClient = New-Object System.Net.WebClient
        $webClient.DownloadFile($downloadUrl, $zipPath)
        Write-Info "Download complete. Extracting..."

        if (Test-Path $extractPath) { Remove-Item $extractPath -Recurse -Force }
        Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

        # Find the bin folder inside the extracted archive
        $binFolder = Get-ChildItem -Path $extractPath -Recurse -Directory -Filter "bin" |
                     Select-Object -First 1

        if (-not $binFolder) { throw "Could not locate 'bin' directory in extracted archive." }

        # Copy to project tools dir
        if (-not (Test-Path $TOOLS_FFMPEG_BIN)) {
            New-Item -ItemType Directory -Path $TOOLS_FFMPEG_BIN -Force | Out-Null
        }
        Get-ChildItem -Path $binFolder.FullName -Filter "*.exe" |
            Copy-Item -Destination $TOOLS_FFMPEG_BIN -Force

        # Cleanup temp files
        Remove-Item $zipPath    -Force -ErrorAction SilentlyContinue
        Remove-Item $extractPath -Recurse -Force -ErrorAction SilentlyContinue

        $env:Path = "$TOOLS_FFMPEG_BIN;$env:Path"
        $testVer = & ffmpeg -version 2>&1 | Select-String 'ffmpeg version' | Select-Object -First 1
        Write-OK "FFmpeg downloaded and installed: $TOOLS_FFMPEG_BIN  ($testVer)"

    } catch {
        Write-Warn "FFmpeg auto-download failed: $_"
        Write-Warn "Video rendering will not work until FFmpeg is installed."
        Write-Info "  Manual install options:"
        Write-Info "    winget install Gyan.FFmpeg"
        Write-Info "    choco install ffmpeg"
        Write-Info "    Manual download: https://ffmpeg.org/download.html"
    }
}

# -----------------------------------------------------------------------------
#  STEP 3 - VIRTUAL ENVIRONMENT MANAGEMENT
# -----------------------------------------------------------------------------
function Test-VenvHealth {
    if (-not (Test-Path $VENV_PYTHON)) { return $false }
    try {
        $result = & "$VENV_PYTHON" -c "import sys; print(sys.version)" 2>&1
        return ($LASTEXITCODE -eq 0 -and $result -match '\d+\.\d+')
    } catch { return $false }
}

function Step-VirtualEnv {
    Write-Step "Virtual Environment Setup"

    if (Test-Path $VENV_DIR) {
        if (Test-VenvHealth) {
            $verInfo = & "$VENV_PYTHON" --version 2>&1
            Write-OK "Existing .venv is healthy  ($verInfo)"
            return
        } else {
            Write-Warn ".venv exists but appears corrupted. Deleting and recreating..."
            try {
                Remove-Item $VENV_DIR -Recurse -Force
                Write-Info "Removed corrupted .venv"
            } catch {
                Write-Fatal "Could not remove corrupted .venv: $_" `
                    "Manually delete the .venv folder and re-run this script."
            }
        }
    }

    Write-Info "Creating virtual environment with: $($script:PythonExe)"
    try {
        & "$($script:PythonExe)" -m venv "$VENV_DIR" 2>&1 | ForEach-Object { Write-Log $_ }
        if ($LASTEXITCODE -ne 0) { throw "python -m venv exited with code $LASTEXITCODE" }
    } catch {
        Write-Fatal "Failed to create virtual environment: $_" `
            "Ensure Python venv module is available: python -m ensurepip --upgrade"
    }

    if (-not (Test-VenvHealth)) {
        Write-Fatal "Virtual environment was created but Python inside it is not functional." `
            "Try deleting .venv manually and re-running this script."
    }

    $verInfo = & "$VENV_PYTHON" --version 2>&1
    Write-OK "Virtual environment created successfully  ($verInfo)"
}

# -----------------------------------------------------------------------------
#  STEP 4 - PIP & DEPENDENCIES
# -----------------------------------------------------------------------------
function Invoke-PipWithRetry {
    param(
        [string[]]$PipArgs,
        [int]$MaxAttempts = 3,
        [string]$Description = "pip command"
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        Write-Info "  Attempt $attempt/$MaxAttempts : pip $($PipArgs -join ' ')"
        try {
            $output = & "$VENV_PYTHON" -m pip @PipArgs 2>&1
            $output | ForEach-Object { Write-Log $_ }
            if ($LASTEXITCODE -eq 0) { return $true }
            Write-Warn "Attempt $attempt failed (exit $LASTEXITCODE)"
        } catch {
            Write-Warn "Attempt $attempt threw exception: $_"
        }
        if ($attempt -lt $MaxAttempts) {
            Write-Info "  Waiting 5 seconds before retry..."
            Start-Sleep -Seconds 5
        }
    }
    return $false
}

function Step-Dependencies {
    Write-Step "Installing Python Dependencies"

    # 1. Upgrade pip / setuptools / wheel
    Write-Info "Upgrading pip, setuptools, wheel..."
    $ok = Invoke-PipWithRetry -PipArgs @('install', '--upgrade', 'pip', 'setuptools', 'wheel') `
                               -Description "pip/setuptools/wheel upgrade"
    if (-not $ok) { Write-Warn "pip upgrade failed - continuing with existing pip" }
    else          { Write-OK "pip / setuptools / wheel upgraded" }

    # 2. Install requirements.txt
    if (-not (Test-Path $REQ_FILE)) {
        Write-Fatal "requirements.txt not found at: $REQ_FILE" "Ensure you're running from the project root."
    }

    Write-Info "Installing requirements.txt  (this may take several minutes)..."
    $ok = Invoke-PipWithRetry -PipArgs @('install', '-r', $REQ_FILE, '--no-warn-script-location') `
                               -Description "requirements.txt"
    if (-not $ok) {
        Write-Err "requirements.txt install failed after 3 attempts."
        Write-Info "Try manually: .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
        $script:Errors += "requirements.txt install failed"
    } else {
        Write-OK "requirements.txt dependencies installed"
    }

    # 3. Install project in editable mode
    Write-Info "Installing shorts_engine package in editable mode (pip install -e .)..."
    $ok = Invoke-PipWithRetry -PipArgs @('install', '-e', '.', '--no-warn-script-location') `
                               -Description "editable package install"
    if (-not $ok) {
        Write-Err "Editable package install (pip install -e .) failed after 3 attempts."
        $script:Errors += "pip install -e . failed"
    } else {
        Write-OK "shorts_engine package installed in editable mode"
    }

    # 4. NVIDIA GPU / CUDA Detection
    $hasGPU = $false
    try {
        $nvSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if ($nvSmi) {
            $gpuInfo = & nvidia-smi --query-gpu=name --format=csv,noheader 2>&1 | Select-Object -First 1
            if ($gpuInfo) {
                $hasGPU = $true
                Write-OK "NVIDIA GPU detected: $gpuInfo"
            }
        }
    } catch { <# no GPU #> }

    if ($GPU -or $hasGPU) {
        if ($GPU -and -not $hasGPU) {
            Write-Info "-GPU flag specified (no nvidia-smi detected, installing anyway)..."
        }
        Write-Info "Reinstalling PyTorch with CUDA 12.1 support..."
        $torchArgs = @(
            'install', '--upgrade',
            'torch', 'torchvision', 'torchaudio',
            '--index-url', 'https://download.pytorch.org/whl/cu121',
            '--no-warn-script-location'
        )
        $ok = Invoke-PipWithRetry -PipArgs $torchArgs -Description "CUDA torch install"
        if ($ok) { Write-OK "PyTorch with CUDA 12.1 installed" }
        else      { Write-Warn "CUDA torch install failed - CPU torch will be used" }
    } else {
        Write-Info "No NVIDIA GPU detected - using CPU PyTorch (re-run with -GPU to force CUDA install)"
    }
}

# -----------------------------------------------------------------------------
#  STEP 5 - ENVIRONMENT CONFIGURATION (.env)
# -----------------------------------------------------------------------------
function New-RandomKey {
    param([int]$Bytes = 32)
    $buf = [byte[]]::new($Bytes)
    [Security.Cryptography.RNGCryptoServiceProvider]::Create().GetBytes($buf)
    return [Convert]::ToBase64String($buf) -replace '[+/=]',''
}

function Step-EnvConfig {
    Write-Step "Environment Configuration (.env)"

    if (Test-Path $ENV_FILE) {
        $sz = (Get-Item $ENV_FILE).Length
        Write-OK ".env already exists ($sz bytes) - preserving your settings"
        return
    }

    if (-not (Test-Path $ENV_EXAMPLE)) {
        Write-Fatal ".env.example template not found!" "Ensure the file exists at: $ENV_EXAMPLE"
    }

    Write-Info "Creating .env from .env.example with cryptographically secure random keys..."

    $content = Get-Content $ENV_EXAMPLE -Raw -Encoding UTF8

    # Generate distinct secure random keys
    $masterKey   = New-RandomKey -Bytes 32
    $sessionKey  = New-RandomKey -Bytes 32
    $saltKey     = New-RandomKey -Bytes 24

    $content = $content -replace 'yt_shorts_dev_master_secret_key_32_bytes_min!',   $masterKey
    $content = $content -replace 'yt_shorts_dev_dashboard_session_secret_32_bytes!', $sessionKey
    $content = $content -replace 'yt_shorts_kdf_salt_2026_secure!',                  $saltKey

    try {
        [System.IO.File]::WriteAllText($ENV_FILE, $content, [System.Text.Encoding]::UTF8)
    } catch {
        Write-Fatal "Failed to write .env file: $_"
    }

    Write-OK ".env created with fresh 256-bit encryption keys"
    Write-Host ""
    Write-Host "  +-------------------------------------------------------------+" -ForegroundColor Yellow
    Write-Host "  |  ACTION REQUIRED: Add your free API keys to .env            |" -ForegroundColor Yellow
    Write-Host "  |                                                             |" -ForegroundColor Yellow
    Write-Host "  |  GROQ_API_KEYS    -> https://console.groq.com/keys           |" -ForegroundColor Yellow
    Write-Host "  |  GEMINI_API_KEYS  -> https://aistudio.google.com/app/apikey  |" -ForegroundColor Yellow
    Write-Host "  |  OPENROUTER_API_KEYS -> https://openrouter.ai/keys           |" -ForegroundColor Yellow
    Write-Host "  |                                                             |" -ForegroundColor Yellow
    Write-Host "  |  File location: $ENV_FILE" -ForegroundColor Yellow
    Write-Host "  +-------------------------------------------------------------+" -ForegroundColor Yellow
    Write-Host ""
}

# -----------------------------------------------------------------------------
#  STEP 6 - DIRECTORY STRUCTURE
# -----------------------------------------------------------------------------
function Step-Directories {
    Write-Step "Storage & Asset Directory Structure"

    $dirs = @(
        "storage",
        "storage\downloads",
        "storage\outputs",
        "storage\temp",
        "storage\thumbnails",
        "storage\models",
        "storage\logs",
        "storage\config",
        "assets",
        "assets\sfx_library",
        "assets\fonts",
        "tools",
        "tools\ffmpeg",
        "tools\ffmpeg\bin"
    )

    foreach ($rel in $dirs) {
        $full = Join-Path $PROJECT_ROOT $rel
        if (-not (Test-Path $full)) {
            New-Item -ItemType Directory -Path $full -Force | Out-Null
            # Add .gitkeep to leaf dirs (not top-level storage/assets)
            if ($rel -notin @('storage','assets','tools','tools\ffmpeg','tools\ffmpeg\bin')) {
                $gk = Join-Path $full ".gitkeep"
                if (-not (Test-Path $gk)) { [System.IO.File]::WriteAllText($gk, '') }
            }
            Write-OK "Created: $rel"
        } else {
            Write-Info "Exists : $rel"
        }
    }

    # Copy Windows emoji font if available
    $winFont = "C:\Windows\Fonts\seguiemj.ttf"
    $dstFont = Join-Path $PROJECT_ROOT "assets\fonts\seguiemj.ttf"
    if ((Test-Path $winFont) -and -not (Test-Path $dstFont)) {
        try {
            Copy-Item $winFont $dstFont -Force
            Write-OK "Copied Windows emoji font -> assets\fonts\seguiemj.ttf"
        } catch {
            Write-Warn "Could not copy emoji font: $_"
        }
    }
}

# -----------------------------------------------------------------------------
#  STEP 7 - DATABASE INITIALIZATION
# -----------------------------------------------------------------------------
function Step-Database {
    Write-Step "Database Initialization"

    # Method 1: run setup_environment.py
    if (Test-Path $SETUP_ENV_PY) {
        Write-Info "Running setup_environment.py..."
        try {
            $output = & "$VENV_PYTHON" "$SETUP_ENV_PY" 2>&1
            $output | ForEach-Object { Write-Log $_; Write-Info "  $_" }
            if ($LASTEXITCODE -eq 0) {
                Write-OK "Database initialized via setup_environment.py"
                # Ensure runtime_settings.json exists
                Step-RuntimeSettings
                return
            }
            Write-Warn "setup_environment.py exited with code $LASTEXITCODE - trying fallback..."
        } catch {
            Write-Warn "setup_environment.py threw exception: $_ - trying fallback..."
        }
    }

    # Method 2: Direct import fallback (use temp script file to avoid here-string path escaping issues)
    Write-Info "Attempting direct DB init import fallback..."
    $tmpScript = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.py'
    try {
        $projectRootFwd = $PROJECT_ROOT -replace '\\', '/'
        $initCode = "import sys, os`nsys.path.insert(0, r'$projectRootFwd')`nos.chdir(r'$projectRootFwd')`ntry:`n    from shorts_engine.db.session import init_db`n    init_db()`n    print('DB_OK')`nexcept Exception as e:`n    print(f'DB_FAIL: {e}')`n    import sys as _sys; _sys.exit(1)`n"
        [System.IO.File]::WriteAllText($tmpScript, $initCode, [System.Text.Encoding]::UTF8)
        $result = & "$VENV_PYTHON" "$tmpScript" 2>&1
        $result | ForEach-Object { Write-Log $_ }
        if ($result -match 'DB_OK') {
            Write-OK "Database initialized via direct import fallback"
        } else {
            Write-Warn "Database init fallback also failed.  DB will be created on first run."
            Write-Warn "Error: $($result | Select-Object -First 3 | Out-String)"
            $script:Warnings += "Database not initialized (will be created on first run)"
        }
    } catch {
        Write-Warn "Database init fallback exception: $_ - DB will be created on first run."
        $script:Warnings += "Database not initialized (will be created on first run)"
    } finally {
        if (Test-Path $tmpScript) { Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue }
    }

    Step-RuntimeSettings
}

function Step-RuntimeSettings {
    if (-not (Test-Path $RUNTIME_CFG)) {
        $dir = Split-Path $RUNTIME_CFG
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $defaultJson = @'
{
  "updated_at": "initial_setup",
  "groq_api_keys": [],
  "gemini_api_keys": [],
  "openrouter_api_keys": [],
  "openai_api_keys": []
}
'@
        [System.IO.File]::WriteAllText($RUNTIME_CFG, $defaultJson, [System.Text.Encoding]::UTF8)
        Write-OK "Created default runtime_settings.json"
    } else {
        Write-Info "runtime_settings.json already exists"
    }
}

# -----------------------------------------------------------------------------
#  STEP 8 - PORT CONFLICT CHECK
# -----------------------------------------------------------------------------
function Step-PortCheck {
    Write-Step "Port 8000 Availability Check"

    try {
        $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if ($listener) {
            $pid8000 = $listener | Select-Object -ExpandProperty OwningProcess -First 1
            $proc    = Get-Process -Id $pid8000 -ErrorAction SilentlyContinue
            $procName = if ($proc) { $proc.ProcessName } else { "Unknown (PID $pid8000)" }
            Write-Warn "Port 8000 is already in use by: $procName (PID: $pid8000)"
            Write-Info "  Options:"
            Write-Info "    1) Kill the process:  Stop-Process -Id $pid8000 -Force"
            Write-Info "    2) Change port:       Edit DASHBOARD_PORT in .env"
            Write-Info "    3) The server may still start on a different port - check .env"

            $answer = Read-Host "  Kill process '$procName' (PID $pid8000) now? [y/N]"
            if ($answer -match '^[Yy]$') {
                try {
                    Stop-Process -Id $pid8000 -Force
                    Start-Sleep -Seconds 1
                    Write-OK "Process terminated. Port 8000 is now free."
                } catch {
                    Write-Warn "Could not kill process: $_"
                }
            } else {
                Write-Info "Leaving process running - dashboard may need a different port."
            }
        } else {
            Write-OK "Port 8000 is free"
        }
    } catch {
        # Get-NetTCPConnection may not be available on all PS versions
        Write-Info "Port check skipped (Get-NetTCPConnection unavailable on this system)"
    }
}

# -----------------------------------------------------------------------------
#  STEP 9 - FINAL VERIFICATION (IMPORT CHECKS)
# -----------------------------------------------------------------------------
function Step-Verification {
    Write-Step "Module Import Verification"

    if ($SkipTests) {
        Write-Warn "Verification skipped (-SkipTests flag)"
        return
    }

    $checks = @(
        @{ Import = 'fastapi';       Label = 'FastAPI'        },
        @{ Import = 'uvicorn';       Label = 'Uvicorn'        },
        @{ Import = 'sqlalchemy';    Label = 'SQLAlchemy'     },
        @{ Import = 'pydantic';      Label = 'Pydantic'       },
        @{ Import = 'cv2';           Label = 'OpenCV (cv2)'   },
        @{ Import = 'torch';         Label = 'PyTorch'        },
        @{ Import = 'faster_whisper';Label = 'Faster-Whisper' },
        @{ Import = 'rich';          Label = 'Rich'           },
        @{ Import = 'shorts_engine'; Label = 'shorts_engine'  }
    )

    $allOk = $true
    foreach ($c in $checks) {
        try {
            $r = & "$VENV_PYTHON" -c "import $($c.Import); print('OK')" 2>&1
            if ($r -match '^OK') {
                Write-OK "$($c.Label)"
            } else {
                Write-Err "$($c.Label) import failed: $r"
                $allOk = $false
            }
        } catch {
            Write-Err "$($c.Label) import threw exception: $_"
            $allOk = $false
        }
    }

    # Smoke test - import the dashboard app
    if (-not $NoServer) {
        Write-Info "Smoke test: importing dashboard app..."
        try {
            $smokeScript = @"
import sys, os
sys.path.insert(0, r'$($PROJECT_ROOT -replace "\\","/")')
os.chdir(r'$($PROJECT_ROOT -replace "\\","/")')
from shorts_engine.dashboard.app import app
print('SMOKE_OK')
"@
            $r = & "$VENV_PYTHON" -c $smokeScript 2>&1
            if ($r -match 'SMOKE_OK') {
                Write-OK "Dashboard app imports cleanly (smoke test passed)"
            } else {
                Write-Warn "Smoke test warnings/errors (non-fatal): $($r | Where-Object { $_ -notmatch 'UserWarning|DeprecationWarning' } | Select-Object -First 3)"
            }
        } catch {
            Write-Warn "Smoke test exception (non-fatal): $_"
        }
    }

    if ($allOk) {
        Write-OK "All critical modules verified"
    } else {
        Write-Err "Some modules failed to import - check log for details"
    }
}

# -----------------------------------------------------------------------------
#  STEP 10 - FINAL SUMMARY & NEXT STEPS
# -----------------------------------------------------------------------------
function Step-Summary {
    Write-Step "Setup Complete - Summary & Next Steps"

    $hasErrors   = $script:Errors.Count   -gt 0
    $hasWarnings = $script:Warnings.Count -gt 0

    if ($hasErrors) {
        Write-Host "`n  ERRORS encountered during setup:" -ForegroundColor Red
        $script:Errors | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red }
    }
    if ($hasWarnings) {
        Write-Host "`n  Warnings (non-fatal):" -ForegroundColor Yellow
        $script:Warnings | ForEach-Object { Write-Host "    - $_" -ForegroundColor Yellow }
    }

    Write-Host ""
    Write-Host "================================================================================" -ForegroundColor Cyan
    if ($hasErrors) {
        Write-Host "  Setup completed WITH ERRORS - review above before running the server" -ForegroundColor Red
    } else {
        Write-Host "  Setup completed SUCCESSFULLY!" -ForegroundColor Green
    }
    Write-Host "================================================================================" -ForegroundColor Cyan

    Write-Host @"

  HOW TO RUN THE PLATFORM
  -------------------------------------------------------------------------------

  1. Activate virtual environment (required every new terminal session):
       .\.venv\Scripts\Activate.ps1

  2. Start the Operator Dashboard (web UI on http://127.0.0.1:8000):
       python run.py dashboard

     Default login -> Username: admin   Password: AdminPass12345!

  3. Start the Background Worker (separate terminal window):
       python run.py worker

  4. Run the test suite:
       python -m pytest tests -q

  5. Process a video via CLI:
       python run.py process --video "https://www.youtube.com/watch?v=VIDEO_ID" --dry-run

  IMPORTANT: Add your API keys to .env  (GROQ, GEMINI, or OPENROUTER)
    -> https://console.groq.com/keys        (free, ultra-fast)
    -> https://aistudio.google.com/app/apikey  (free Gemini)
    -> https://openrouter.ai/keys           (free models)

  Log file: $LOG_FILE
  -------------------------------------------------------------------------------
"@ -ForegroundColor Gray

    Write-Log "Setup finished. Errors: $($script:Errors.Count)  Warnings: $($script:Warnings.Count)"
}

# -----------------------------------------------------------------------------
#  MAIN ENTRY POINT
# -----------------------------------------------------------------------------
function Main {
    # Make sure we run from the project root (handles any CWD)
    Set-Location $PROJECT_ROOT

    Initialize-Logging
    Show-Banner

    Write-Log "Starting setup. Total steps: $TOTAL_STEPS"

    Step-Python
    Step-FFmpeg
    Step-VirtualEnv
    Step-Dependencies
    Step-EnvConfig
    Step-Directories
    Step-Database
    Step-PortCheck
    Step-Verification
    Step-Summary
}

# -----------------------------------------------------------------------------
#  GLOBAL EXCEPTION HANDLER
# -----------------------------------------------------------------------------
try {
    Main
} catch {
    $msg = $_.Exception.Message
    $line = $_.InvocationInfo.ScriptLineNumber
    Write-Host "`n[FATAL UNHANDLED ERROR at line $line] $msg" -ForegroundColor Red
    Write-Host "Stack trace:" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed
    if (Test-Path $LOG_FILE) {
        Write-Log "[FATAL UNHANDLED at line $line] $msg"
        Write-Log $_.ScriptStackTrace
        Write-Host "`nFull log saved to: $LOG_FILE" -ForegroundColor Yellow
    }
    Read-Host "Press ENTER to exit"
    exit 1
}
